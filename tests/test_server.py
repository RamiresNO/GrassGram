from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from messenger.server import create_messenger_server
from messenger.service import MessengerService
from messenger.storage import JsonRepository

PUBLIC_DIRECTORY = Path(__file__).resolve().parent.parent / "public"


class ServerTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory(prefix="study-hall-http-")
        self.addCleanup(tmp.cleanup)
        messenger = MessengerService.open(
            JsonRepository(Path(tmp.name) / "messages.json")
        )
        self.server = create_messenger_server(messenger, PUBLIC_DIRECTORY, "127.0.0.1", 0)
        thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.base = f"http://127.0.0.1:{self.server.server_address[1]}"

    def request(self, method: str, route: str, body=None, raw: bytes | None = None):
        data = raw if raw is not None else (
            json.dumps(body).encode("utf-8") if body is not None else None
        )
        request = urllib.request.Request(
            f"{self.base}{route}",
            data=data,
            method=method,
            headers={"Content-Type": "application/json"} if data is not None else {},
        )
        try:
            with urllib.request.urlopen(request) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as error:
            return error.code, error.headers, error.read()

    def json(self, method: str, route: str, body=None, raw: bytes | None = None):
        status, _, payload = self.request(method, route, body, raw)
        return status, json.loads(payload)


class ServerTests(ServerTestCase):
    def test_serves_the_web_ui_and_its_static_assets(self) -> None:
        status, headers, page = self.request("GET", "/")
        self.assertEqual(status, 200)
        page_text = page.decode("utf-8")
        self.assertIn("grassgram — простір для розмов", page_text)
        self.assertIn('class="app-rail"', page_text)
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(headers["X-Frame-Options"], "DENY")
        self.assertIn("default-src 'self'", headers["Content-Security-Policy"])

        status, headers, _ = self.request("GET", "/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers["Content-Type"])
        status, headers, _ = self.request("GET", "/styles.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers["Content-Type"])

    def test_account_group_chat_sending_search_and_access_checks(self) -> None:
        status, alice = self.json("POST", "/api/users", {"username": "alice"})
        self.assertEqual(status, 201)
        _, bob = self.json("POST", "/api/users", {"username": "bob"})
        _, outsider = self.json("POST", "/api/users", {"username": "outsider"})
        alice, bob, outsider = alice["user"], bob["user"], outsider["user"]

        status, created = self.json(
            "POST",
            "/api/conversations",
            {"title": "Підготовка до іспиту", "participantIds": [alice["id"], bob["id"]]},
        )
        self.assertEqual(status, 201)
        conversation = created["conversation"]
        self.assertEqual(conversation["participantIds"], [alice["id"], bob["id"]])

        status, sent = self.json(
            "POST",
            f"/api/conversations/{conversation['id']}/messages",
            {"senderId": alice["id"], "text": "Повторімо біологію"},
        )
        self.assertEqual(status, 201)
        self.assertEqual(sent["message"]["conversationId"], conversation["id"])

        status, history = self.json(
            "GET", f"/api/conversations/{conversation['id']}/messages?userId={bob['id']}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(history["messages"]), 1)

        from urllib.parse import quote

        status, found = self.json(
            "GET", f"/api/search?userId={alice['id']}&q={quote('БІОЛОГІЮ')}"
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(found["messages"]), 1)

        status, _ = self.json(
            "GET", f"/api/conversations/{conversation['id']}/messages?userId={outsider['id']}"
        )
        self.assertEqual(status, 404)

        status, _ = self.json(
            "POST",
            f"/api/conversations/{conversation['id']}/messages",
            {"senderId": outsider["id"], "text": "Не маю доступу"},
        )
        self.assertEqual(status, 400)

        status, listing = self.json("GET", f"/api/conversations?userId={outsider['id']}")
        self.assertEqual((status, listing["conversations"]), (200, []))
        status, listing = self.json("GET", f"/api/conversations?userId={alice['id']}")
        self.assertEqual(len(listing["conversations"]), 1)

        status, users = self.json("GET", "/api/users")
        self.assertEqual([u["username"] for u in users["users"]], ["alice", "bob", "outsider"])

    def test_error_responses(self) -> None:
        status, error = self.json("POST", "/api/users", raw=b"{")
        self.assertEqual((status, error["error"]), (400, "Некоректний формат JSON."))
        status, error = self.json("POST", "/api/users", raw=b"")
        self.assertEqual((status, error["error"]), (400, "Некоректний формат JSON."))
        status, _ = self.json("POST", "/api/users", raw=b"[1, 2]")
        self.assertEqual(status, 400)
        status, _ = self.json("POST", "/api/users", {"username": 42})
        self.assertEqual(status, 400)
        status, _ = self.json("POST", "/api/users", {"username": "bad name"})
        self.assertEqual(status, 400)

        self.json("POST", "/api/users", {"username": "alice"})
        status, error = self.json("POST", "/api/users", {"username": "ALICE"})
        self.assertEqual((status, error["error"]), (409, "Це ім’я вже зайняте."))

        status, error = self.json("POST", "/api/users", {"username": "x" * 20_000})
        self.assertEqual((status, error["error"]), (400, "Запит завеликий."))

        status, _ = self.json("POST", "/api/conversations", {"title": "T", "participantIds": [1]})
        self.assertEqual(status, 400)
        status, _ = self.json("GET", "/api/conversations")
        self.assertEqual(status, 404)
        status, _ = self.json("GET", "/api/search?userId=nobody&q=x")
        self.assertEqual(status, 404)
        status, error = self.json("GET", "/api/unknown")
        self.assertEqual((status, error["error"]), (404, "Маршрут не знайдено."))
        status, error = self.json("DELETE", "/api/users")
        self.assertEqual(status, 404)
        status, error = self.json("GET", "/nope")
        self.assertEqual((status, error["error"]), (404, "Сторінку не знайдено."))

    def test_empty_search_query_is_rejected(self) -> None:
        _, created = self.json("POST", "/api/users", {"username": "alice"})
        status, error = self.json("GET", f"/api/search?userId={created['user']['id']}&q=")
        self.assertEqual((status, error["error"]), (400, "Введіть текст для пошуку."))


if __name__ == "__main__":
    unittest.main()
