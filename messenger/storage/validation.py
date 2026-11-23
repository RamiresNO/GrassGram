from __future__ import annotations

from datetime import datetime
from typing import Any

from ..models import Conversation, Message, MessengerData, User


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_non_empty_str(value: Any) -> bool:
    return isinstance(value, str) and len(value) > 0


def _parse_user(value: Any) -> User | None:
    if not isinstance(value, dict):
        return None
    if not (
        _is_non_empty_str(value.get("id"))
        and _is_non_empty_str(value.get("username"))
        and _is_timestamp(value.get("createdAt"))
    ):
        return None
    return User(value["id"], value["username"], value["createdAt"])


def _parse_conversation(value: Any) -> Conversation | None:
    if not isinstance(value, dict):
        return None
    participants = value.get("participantIds")
    title = value.get("title")
    if not (
        _is_non_empty_str(value.get("id"))
        and isinstance(title, str)
        and title.strip()
        and isinstance(participants, list)
        and all(_is_non_empty_str(p) for p in participants)
        and _is_timestamp(value.get("createdAt"))
    ):
        return None
    return Conversation(value["id"], title, tuple(participants), value["createdAt"])


def _parse_message(value: Any) -> Message | None:
    if not isinstance(value, dict):
        return None
    text = value.get("text")
    if not (
        _is_non_empty_str(value.get("id"))
        and _is_non_empty_str(value.get("conversationId"))
        and _is_non_empty_str(value.get("senderId"))
        and isinstance(text, str)
        and text.strip()
        and _is_timestamp(value.get("createdAt"))
    ):
        return None
    return Message(
        value["id"],
        value["conversationId"],
        value["senderId"],
        text,
        value["createdAt"],
    )


def parse_messenger_data(raw: Any) -> MessengerData | None:
    """Перевіряє структуру та цілісність даних.

    Повертає ``MessengerData`` або ``None``, якщо формат некоректний:
    неправильні типи полів, дублікати id чи імен, учасники-«привиди»,
    повідомлення від не-учасників розмови тощо.
    """
    if not isinstance(raw, dict):
        return None
    raw_users = raw.get("users")
    raw_conversations = raw.get("conversations")
    raw_messages = raw.get("messages")
    if not (
        isinstance(raw_users, list)
        and isinstance(raw_conversations, list)
        and isinstance(raw_messages, list)
    ):
        return None

    users = [_parse_user(item) for item in raw_users]
    conversations = [_parse_conversation(item) for item in raw_conversations]
    messages = [_parse_message(item) for item in raw_messages]
    if (
        any(u is None for u in users)
        or any(c is None for c in conversations)
        or any(m is None for m in messages)
    ):
        return None

    user_ids = {u.id for u in users}  # type: ignore[union-attr]
    conversation_by_id = {c.id: c for c in conversations}  # type: ignore[union-attr]
    if (
        len(user_ids) != len(users)
        or len(conversation_by_id) != len(conversations)
        or len({u.username.lower() for u in users}) != len(users)  # type: ignore[union-attr]
    ):
        return None

    for conversation in conversations:
        assert conversation is not None
        ids = conversation.participant_ids
        if len(ids) < 2 or len(set(ids)) != len(ids) or any(i not in user_ids for i in ids):
            return None

    for message in messages:
        assert message is not None
        conversation = conversation_by_id.get(message.conversation_id)
        if conversation is None or message.sender_id not in conversation.participant_ids:
            return None
    if len({m.id for m in messages}) != len(messages):  # type: ignore[union-attr]
        return None

    return MessengerData(tuple(users), tuple(conversations), tuple(messages))  # type: ignore[arg-type]
