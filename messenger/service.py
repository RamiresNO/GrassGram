from __future__ import annotations

import re
import threading
import uuid
from dataclasses import replace
from typing import Protocol

from .errors import MessengerError
from .models import Conversation, Message, MessengerData, User, now_iso

MAX_USERNAME_LENGTH = 30
MAX_TITLE_LENGTH = 80
MAX_MESSAGE_LENGTH = 4000

_USERNAME_PATTERN = re.compile(r"[a-zA-Z0-9_.-]+")


class Repository(Protocol):
    def load(self) -> MessengerData: ...

    def save(self, data: MessengerData) -> None: ...


def _js_length(text: str) -> int:
    """Довжина рядка в UTF-16 кодових одиницях.

    Так рахують довжину браузер (``maxlength``) і оригінальний TypeScript-код,
    тому емодзі рахуються за два символи в усіх частинах застосунку.
    """
    return len(text.encode("utf-16-le")) // 2


class MessengerService:
    """Уся бізнес-логіка месенджера. Потокобезпечна: записи йдуть під замком."""

    def __init__(self, repository: Repository, data: MessengerData) -> None:
        self._repository = repository
        self._data = data
        self._lock = threading.RLock()

    @classmethod
    def open(cls, repository: Repository) -> "MessengerService":
        return cls(repository, repository.load())

    # --- користувачі -----------------------------------------------------

    def list_users(self) -> list[User]:
        return sorted(self._data.users, key=lambda user: user.username.casefold())

    def find_user_by_username(self, username: str) -> User | None:
        normalized = username.strip().lower()
        for user in self._data.users:
            if user.username.lower() == normalized:
                return user
        return None

    def get_user(self, user_id: str) -> User | None:
        for user in self._data.users:
            if user.id == user_id:
                return user
        return None

    def create_user(self, username: str) -> User:
        normalized = username.strip()
        if not _USERNAME_PATTERN.fullmatch(normalized):
            raise MessengerError(
                "Ім’я може містити лише латинські літери, цифри, крапки, дефіси та підкреслення."
            )
        if len(normalized) > MAX_USERNAME_LENGTH:
            raise MessengerError(
                f"Ім’я не може бути довшим за {MAX_USERNAME_LENGTH} символів."
            )

        with self._lock:
            if self.find_user_by_username(normalized):
                raise MessengerError("Це ім’я вже зайняте.", 409)
            user = User(id=str(uuid.uuid4()), username=normalized, created_at=now_iso())
            self._commit(replace(self._data, users=(*self._data.users, user)))
            return user

    # --- розмови ---------------------------------------------------------

    def list_conversations_for_user(self, user_id: str) -> list[Conversation]:
        return sorted(
            (c for c in self._data.conversations if user_id in c.participant_ids),
            key=lambda conversation: conversation.title.casefold(),
        )

    def get_conversation(self, conversation_id: str) -> Conversation | None:
        for conversation in self._data.conversations:
            if conversation.id == conversation_id:
                return conversation
        return None

    def get_participants(self, conversation: Conversation) -> list[User]:
        users_by_id = {user.id: user for user in self._data.users}
        return [
            users_by_id[user_id]
            for user_id in conversation.participant_ids
            if user_id in users_by_id
        ]

    def create_conversation(
        self, title: str, participant_ids: list[str]
    ) -> Conversation:
        normalized_title = title.strip()
        if not normalized_title:
            raise MessengerError("Назва розмови не може бути порожньою.")
        if _js_length(normalized_title) > MAX_TITLE_LENGTH:
            raise MessengerError(
                f"Назва не може бути довшою за {MAX_TITLE_LENGTH} символів."
            )

        unique_ids = tuple(dict.fromkeys(participant_ids))  # зберігає порядок
        if len(unique_ids) < 2:
            raise MessengerError("У розмові мають бути щонайменше двоє учасників.")

        with self._lock:
            known_ids = {user.id for user in self._data.users}
            if any(user_id not in known_ids for user_id in unique_ids):
                raise MessengerError(
                    "Усі учасники мають бути зареєстрованими користувачами."
                )
            conversation = Conversation(
                id=str(uuid.uuid4()),
                title=normalized_title,
                participant_ids=unique_ids,
                created_at=now_iso(),
            )
            self._commit(
                replace(
                    self._data,
                    conversations=(*self._data.conversations, conversation),
                )
            )
            return conversation

    # --- повідомлення ----------------------------------------------------

    def get_messages(self, conversation_id: str) -> list[Message]:
        return sorted(
            (m for m in self._data.messages if m.conversation_id == conversation_id),
            key=lambda message: message.created_at,
        )

    def send_message(self, conversation_id: str, sender_id: str, text: str) -> Message:
        with self._lock:
            conversation = self.get_conversation(conversation_id)
            if conversation is None:
                raise MessengerError("Розмову не знайдено.", 404)
            if sender_id not in conversation.participant_ids:
                raise MessengerError(
                    "Повідомлення можуть надсилати лише учасники розмови."
                )
            if not text.strip():
                raise MessengerError("Повідомлення не може бути порожнім.")
            if _js_length(text) > MAX_MESSAGE_LENGTH:
                raise MessengerError(
                    f"Повідомлення не може бути довшим за {MAX_MESSAGE_LENGTH} символів."
                )

            message = Message(
                id=str(uuid.uuid4()),
                conversation_id=conversation_id,
                sender_id=sender_id,
                text=text,
                created_at=now_iso(),
            )
            self._commit(replace(self._data, messages=(*self._data.messages, message)))
            return message

    def search_messages(self, user_id: str, query: str) -> list[Message]:
        normalized_query = query.strip().lower()
        if not normalized_query:
            raise MessengerError("Введіть текст для пошуку.")

        visible_ids = {c.id for c in self.list_conversations_for_user(user_id)}
        return sorted(
            (
                m
                for m in self._data.messages
                if m.conversation_id in visible_ids
                and normalized_query in m.text.lower()
            ),
            key=lambda message: message.created_at,
        )

    # --- внутрішнє -------------------------------------------------------

    def _commit(self, next_data: MessengerData) -> None:
        """Спершу зберігаємо, потім оновлюємо пам'ять: збій запису не ламає стан."""
        self._repository.save(next_data)
        self._data = next_data
