from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from messenger.errors import MessengerError
from messenger.service import MessengerService
from messenger.storage import JsonRepository, SqliteRepository


class ServiceTestCase(unittest.TestCase):
    """Базовий клас: тимчасова папка і готовий сервіс над JSON-сховищем."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="study-hall-")
        self.addCleanup(self._tmp.cleanup)
        self.directory = Path(self._tmp.name)
        self.file_path = self.directory / "data" / "messages.json"
        self.repository = JsonRepository(self.file_path)
        self.service = MessengerService.open(self.repository)


class MessengerServiceTests(ServiceTestCase):
    def test_persists_users_group_conversations_and_ordered_messages(self) -> None:
        alice = self.service.create_user("alice")
        bob = self.service.create_user("bob")
        chris = self.service.create_user("chris")
        conversation = self.service.create_conversation(
            "Exam prep", [alice.id, bob.id, chris.id]
        )
        self.service.send_message(conversation.id, alice.id, "Bring your notes")
        self.service.send_message(conversation.id, bob.id, "I will bring mine")

        reloaded = MessengerService.open(self.repository)
        self.assertEqual(
            [m.text for m in reloaded.get_messages(conversation.id)],
            ["Bring your notes", "I will bring mine"],
        )
        self.assertEqual(len(reloaded.get_participants(conversation)), 3)

    def test_validates_users_conversations_and_messages(self) -> None:
        alice = self.service.create_user("alice")
        bob = self.service.create_user("bob")

        with self.assertRaisesRegex(MessengerError, "вже зайняте"):
            self.service.create_user("ALICE")
        with self.assertRaisesRegex(MessengerError, "латинські літери"):
            self.service.create_user("not a username")
        with self.assertRaisesRegex(MessengerError, "латинські літери"):
            self.service.create_user("")
        with self.assertRaisesRegex(MessengerError, "довшим за 30"):
            self.service.create_user("a" * 31)
        with self.assertRaisesRegex(MessengerError, "щонайменше двоє учасників"):
            self.service.create_conversation("Solo", [alice.id])
        with self.assertRaisesRegex(MessengerError, "щонайменше двоє учасників"):
            self.service.create_conversation("Dup", [alice.id, alice.id])
        with self.assertRaisesRegex(MessengerError, "зареєстрованими"):
            self.service.create_conversation("Ghost", [alice.id, "ghost"])
        with self.assertRaisesRegex(MessengerError, "не може бути порожньою"):
            self.service.create_conversation("   ", [alice.id, bob.id])
        with self.assertRaisesRegex(MessengerError, "довшою за 80"):
            self.service.create_conversation("x" * 81, [alice.id, bob.id])

        conversation = self.service.create_conversation("Study group", [alice.id, bob.id])
        with self.assertRaisesRegex(MessengerError, "не може бути порожнім"):
            self.service.send_message(conversation.id, alice.id, "  ")
        with self.assertRaisesRegex(MessengerError, "лише учасники розмови"):
            self.service.send_message(conversation.id, "not-a-participant", "Hello")
        with self.assertRaisesRegex(MessengerError, "довшим за 4000"):
            self.service.send_message(conversation.id, alice.id, "x" * 4001)
        with self.assertRaises(MessengerError) as caught:
            self.service.send_message("missing", alice.id, "Hello")
        self.assertEqual(caught.exception.status_code, 404)

    def test_duplicate_username_has_conflict_status(self) -> None:
        self.service.create_user("alice")
        with self.assertRaises(MessengerError) as caught:
            self.service.create_user("Alice")
        self.assertEqual(caught.exception.status_code, 409)

    def test_limits_count_utf16_units_like_the_browser(self) -> None:
        alice = self.service.create_user("alice")
        bob = self.service.create_user("bob")
        conversation = self.service.create_conversation("Chat", [alice.id, bob.id])
        self.service.send_message(conversation.id, alice.id, "x" * 4000)  # рівно ліміт
        # 2001 емодзі = 4002 одиниці UTF-16 у браузері
        with self.assertRaisesRegex(MessengerError, "довшим за 4000"):
            self.service.send_message(conversation.id, alice.id, "😀" * 2001)

    def test_searches_case_insensitively_only_in_users_conversations(self) -> None:
        alice = self.service.create_user("alice")
        bob = self.service.create_user("bob")
        chris = self.service.create_user("chris")
        shared = self.service.create_conversation("Shared", [alice.id, bob.id])
        private = self.service.create_conversation("Chris and Bob", [chris.id, bob.id])
        self.service.send_message(shared.id, bob.id, "Bring the BIOLOGY book")
        self.service.send_message(private.id, chris.id, "Biology notes")

        self.assertEqual(len(self.service.search_messages(alice.id, "biology")), 1)
        self.assertEqual(len(self.service.search_messages(chris.id, "BIOLOGY")), 1)
        with self.assertRaisesRegex(MessengerError, "для пошуку"):
            self.service.search_messages(alice.id, "   ")

    def test_lists_are_sorted_and_scoped(self) -> None:
        bob = self.service.create_user("bob")
        alice = self.service.create_user("Alice")
        chris = self.service.create_user("chris")
        self.assertEqual(
            [u.username for u in self.service.list_users()], ["Alice", "bob", "chris"]
        )
        self.service.create_conversation("Zebra", [alice.id, bob.id])
        self.service.create_conversation("apple", [alice.id, bob.id])
        self.service.create_conversation("Hidden", [bob.id, chris.id])
        self.assertEqual(
            [c.title for c in self.service.list_conversations_for_user(alice.id)],
            ["apple", "Zebra"],
        )


class JsonRepositoryTests(ServiceTestCase):
    def test_persists_valid_json_and_rejects_malformed_data(self) -> None:
        from messenger.models import empty_messenger_data

        self.repository.save(empty_messenger_data())
        self.assertEqual(self.repository.load(), empty_messenger_data())
        self.assertIn('"conversations": []', self.file_path.read_text("utf-8"))

        self.file_path.write_text('{"users": []}', "utf-8")
        with self.assertRaisesRegex(ValueError, "invalid format"):
            self.repository.load()

        self.file_path.write_text("{", "utf-8")
        with self.assertRaisesRegex(ValueError, "Could not parse"):
            self.repository.load()

    def test_missing_file_means_empty_data_and_no_temp_files_remain(self) -> None:
        self.assertEqual(self.repository.load().users, ())
        self.service.create_user("alice")
        self.assertEqual([p.name for p in self.file_path.parent.iterdir()], ["messages.json"])

    def test_rejects_inconsistent_data(self) -> None:
        stamp = "2026-01-01T00:00:00.000Z"
        users = [
            {"id": "1", "username": "alice", "createdAt": stamp},
            {"id": "2", "username": "bob", "createdAt": stamp},
        ]
        conversation = {
            "id": "c", "title": "T", "participantIds": ["1", "2"], "createdAt": stamp,
        }
        outsider_message = {
            "id": "m", "conversationId": "c", "senderId": "3", "text": "hi", "createdAt": stamp,
        }
        cases = {
            "duplicate usernames (case-insensitive)": {
                "users": [users[0], {**users[1], "username": "ALICE"}],
                "conversations": [], "messages": [],
            },
            "unknown participant": {
                "users": users,
                "conversations": [{**conversation, "participantIds": ["1", "9"]}],
                "messages": [],
            },
            "sender outside conversation": {
                "users": users, "conversations": [conversation], "messages": [outsider_message],
            },
            "bad timestamp": {
                "users": [{**users[0], "createdAt": "yesterday"}],
                "conversations": [], "messages": [],
            },
        }
        for name, payload in cases.items():
            with self.subTest(name):
                self.file_path.parent.mkdir(parents=True, exist_ok=True)
                self.file_path.write_text(json.dumps(payload), "utf-8")
                with self.assertRaisesRegex(ValueError, "invalid format"):
                    self.repository.load()

    def test_json_file_uses_camel_case_keys_compatible_with_typescript_version(self) -> None:
        alice = self.service.create_user("alice")
        bob = self.service.create_user("bob")
        conversation = self.service.create_conversation("Чат", [alice.id, bob.id])
        self.service.send_message(conversation.id, alice.id, "Привіт")
        raw = json.loads(self.file_path.read_text("utf-8"))
        self.assertEqual(raw["users"][0].keys(), {"id", "username", "createdAt"})
        self.assertEqual(
            raw["conversations"][0].keys(), {"id", "title", "participantIds", "createdAt"}
        )
        self.assertEqual(
            raw["messages"][0].keys(),
            {"id", "conversationId", "senderId", "text", "createdAt"},
        )
        self.assertIn("Привіт", self.file_path.read_text("utf-8"))  # без \u-екранування


class SqliteRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="study-hall-sqlite-")
        self.addCleanup(self._tmp.cleanup)
        self.db_path = Path(self._tmp.name) / "nested" / "messenger.db"

    def test_persists_everything_across_reopen(self) -> None:
        service = MessengerService.open(SqliteRepository(self.db_path))
        alice = service.create_user("alice")
        bob = service.create_user("bob")
        conversation = service.create_conversation("Exam prep", [alice.id, bob.id])
        service.send_message(conversation.id, alice.id, "Перший")
        service.send_message(conversation.id, bob.id, "Другий")

        reopened = MessengerService.open(SqliteRepository(self.db_path))
        self.assertEqual([u.username for u in reopened.list_users()], ["alice", "bob"])
        self.assertEqual(
            [m.text for m in reopened.get_messages(conversation.id)], ["Перший", "Другий"]
        )
        stored = reopened.get_conversation(conversation.id)
        self.assertEqual(stored.participant_ids, (alice.id, bob.id))

    def test_migrations_are_applied_once_and_tracked(self) -> None:
        import sqlite3

        repository = SqliteRepository(self.db_path)
        repository.load()
        repository.load()
        with sqlite3.connect(self.db_path) as connection:
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 1)
            tables = {
                r[0]
                for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
        self.assertTrue(
            {"users", "conversations", "conversation_participants", "messages"} <= tables
        )

    def test_database_enforces_case_insensitive_unique_usernames(self) -> None:
        import sqlite3
        from dataclasses import replace

        from messenger.models import User

        repository = SqliteRepository(self.db_path)
        service = MessengerService.open(repository)
        service.create_user("alice")
        clash = User("x", "ALICE", "2026-01-01T00:00:00.000Z")
        with self.assertRaises(sqlite3.IntegrityError):
            repository.save(replace(service._data, users=(*service._data.users, clash)))


if __name__ == "__main__":
    unittest.main()
