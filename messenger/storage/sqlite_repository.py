from __future__ import annotations

import os
import re
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from ..models import MessengerData, empty_messenger_data
from .validation import parse_messenger_data

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"


class SqliteRepository:
    """Сховище на SQLite — Python-аналог Cloudflare D1 з оригінального проєкту.

    Використовує ті самі SQL-міграції з каталогу ``migrations/``. Застосовані
    міграції відстежуються через ``PRAGMA user_version``.
    """

    def __init__(
        self,
        database_path: str | os.PathLike[str],
        migrations_dir: str | os.PathLike[str] | None = None,
    ) -> None:
        self._database_path = Path(database_path)
        self._migrations_dir = (
            Path(migrations_dir) if migrations_dir else DEFAULT_MIGRATIONS_DIR
        )

    def _connect(self) -> sqlite3.Connection:
        self._database_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self._database_path)
        connection.execute("PRAGMA foreign_keys = ON")
        self._migrate(connection)
        return connection

    def _migrate(self, connection: sqlite3.Connection) -> None:
        applied = connection.execute("PRAGMA user_version").fetchone()[0]
        files = sorted(self._migrations_dir.glob("*.sql"))
        for number, path in enumerate(files, start=1):
            if number <= applied:
                continue
            script = path.read_text(encoding="utf-8")
            if sqlite3.sqlite_version_info < (3, 37, 0):
                # STRICT-таблиці з'явилися в SQLite 3.37.
                script = re.sub(r"\)\s*STRICT\s*;", ");", script)
            connection.executescript(
                f"BEGIN;\n{script}\nPRAGMA user_version = {number};\nCOMMIT;"
            )

    def load(self) -> MessengerData:
        if not self._database_path.exists():
            self._connect().close()
            return empty_messenger_data()

        with closing(self._connect()) as connection:
            users = [
                {"id": r[0], "username": r[1], "createdAt": r[2]}
                for r in connection.execute(
                    "SELECT id, username, created_at FROM users ORDER BY rowid"
                )
            ]
            participants: dict[str, list[str]] = {}
            for conversation_id, user_id in connection.execute(
                "SELECT conversation_id, user_id FROM conversation_participants ORDER BY rowid"
            ):
                participants.setdefault(conversation_id, []).append(user_id)
            conversations = [
                {
                    "id": r[0],
                    "title": r[1],
                    "participantIds": participants.get(r[0], []),
                    "createdAt": r[2],
                }
                for r in connection.execute(
                    "SELECT id, title, created_at FROM conversations ORDER BY rowid"
                )
            ]
            messages = [
                {
                    "id": r[0],
                    "conversationId": r[1],
                    "senderId": r[2],
                    "text": r[3],
                    "createdAt": r[4],
                }
                for r in connection.execute(
                    "SELECT id, conversation_id, sender_id, text, created_at "
                    "FROM messages ORDER BY rowid"
                )
            ]

        data = parse_messenger_data(
            {"users": users, "conversations": conversations, "messages": messages}
        )
        if data is None:
            raise ValueError(
                f"Messenger data in {self._database_path} has an invalid format."
            )
        return data

    def save(self, data: MessengerData) -> None:
        """Дописує до бази лише нові записи (дані месенджера лише додаються)."""
        with closing(self._connect()) as connection:
            known: dict[str, set[Any]] = {
                "users": {r[0] for r in connection.execute("SELECT id FROM users")},
                "conversations": {
                    r[0] for r in connection.execute("SELECT id FROM conversations")
                },
                "messages": {r[0] for r in connection.execute("SELECT id FROM messages")},
            }
            with connection:  # одна транзакція: або все, або нічого
                for user in data.users:
                    if user.id not in known["users"]:
                        connection.execute(
                            "INSERT INTO users (id, username, created_at) VALUES (?, ?, ?)",
                            (user.id, user.username, user.created_at),
                        )
                for conversation in data.conversations:
                    if conversation.id in known["conversations"]:
                        continue
                    connection.execute(
                        "INSERT INTO conversations (id, title, created_at) VALUES (?, ?, ?)",
                        (conversation.id, conversation.title, conversation.created_at),
                    )
                    connection.executemany(
                        "INSERT INTO conversation_participants (conversation_id, user_id) "
                        "VALUES (?, ?)",
                        [(conversation.id, uid) for uid in conversation.participant_ids],
                    )
                for message in data.messages:
                    if message.id not in known["messages"]:
                        connection.execute(
                            "INSERT INTO messages (id, conversation_id, sender_id, text, created_at) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (
                                message.id,
                                message.conversation_id,
                                message.sender_id,
                                message.text,
                                message.created_at,
                            ),
                        )
