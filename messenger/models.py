from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    """Поточний час UTC у форматі ISO 8601 з мілісекундами (як ``Date.toISOString``)."""
    moment = datetime.now(timezone.utc)
    return moment.isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class User:
    id: str
    username: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "username": self.username, "createdAt": self.created_at}


@dataclass(frozen=True)
class Conversation:
    id: str
    title: str
    participant_ids: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "participantIds": list(self.participant_ids),
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class Message:
    id: str
    conversation_id: str
    sender_id: str
    text: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "conversationId": self.conversation_id,
            "senderId": self.sender_id,
            "text": self.text,
            "createdAt": self.created_at,
        }


@dataclass(frozen=True)
class MessengerData:
    """Повний знімок даних месенджера. Колекції вважаються незмінними."""

    users: tuple[User, ...] = field(default_factory=tuple)
    conversations: tuple[Conversation, ...] = field(default_factory=tuple)
    messages: tuple[Message, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "users": [user.to_dict() for user in self.users],
            "conversations": [c.to_dict() for c in self.conversations],
            "messages": [m.to_dict() for m in self.messages],
        }


def empty_messenger_data() -> MessengerData:
    return MessengerData()
