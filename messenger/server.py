from __future__ import annotations

import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .errors import MessengerError
from .service import MessengerService

MAX_REQUEST_BYTES = 16 * 1024

STATIC_FILES = {
    "/": "index.html",
    "/styles.css": "styles.css",
    "/app.js": "app.js",
}
CONTENT_TYPES = {
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}
DEFAULT_CONTENT_TYPE = "text/html; charset=utf-8"

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'"
    ),
}

MESSAGES_ROUTE = re.compile(r"^/api/conversations/([^/]+)/messages$")
CHECK_FIELDS_MESSAGE = "Перевірте заповнені поля та спробуйте ще раз."


class _InvalidJson(Exception):
    """Тіло запиту порожнє або не є коректним JSON."""


def _require_string(body: dict[str, Any], key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str):
        raise MessengerError(CHECK_FIELDS_MESSAGE)
    return value


def create_messenger_server(
    messenger: MessengerService,
    public_directory: str | Path,
    host: str = "127.0.0.1",
    port: int = 3000,
) -> ThreadingHTTPServer:
    """Створює HTTP-сервер месенджера (виклич ``serve_forever()`` для запуску)."""
    public_path = Path(public_directory)

    class Handler(BaseHTTPRequestHandler):
        server_version = "StudyHallMessenger"

        # --- відповіді ---------------------------------------------------

        def _send(
            self, status: int, content_type: str, body: bytes, cache: str
        ) -> None:
            self.send_response(status)
            for name, value in SECURITY_HEADERS.items():
                self.send_header(name, value)
            self.send_header("Content-Type", content_type)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _send_json(self, status: int, payload: Any) -> None:
            body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            self._send(status, "application/json; charset=utf-8", body, "no-store")

        def _send_error(self, error: Exception) -> None:
            if isinstance(error, MessengerError):
                self._send_json(error.status_code, {"error": error.message})
            elif isinstance(error, _InvalidJson):
                self._send_json(400, {"error": "Некоректний формат JSON."})
            else:
                self.log_error("Messenger request failed: %r", error)
                self._send_json(500, {"error": "Сталася внутрішня помилка сервера."})

        # --- допоміжне ---------------------------------------------------

        def _read_json(self) -> Any:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                raise _InvalidJson() from None
            if length > MAX_REQUEST_BYTES:
                raise MessengerError("Запит завеликий.")
            if length <= 0:
                raise _InvalidJson()
            raw = self.rfile.read(length)
            try:
                return json.loads(raw.decode("utf-8"))
            except ValueError:  # JSONDecodeError і UnicodeDecodeError
                raise _InvalidJson() from None

        def _read_object(self) -> dict[str, Any]:
            body = self._read_json()
            if not isinstance(body, dict):
                raise MessengerError(CHECK_FIELDS_MESSAGE)
            return body

        def _serve_static(self, file_name: str) -> None:
            contents = (public_path / file_name).read_bytes()
            content_type = CONTENT_TYPES.get(Path(file_name).suffix, DEFAULT_CONTENT_TYPE)
            self._send(200, content_type, contents, "no-cache")

        # --- маршрутизація -----------------------------------------------

        def _dispatch(self) -> None:
            try:
                self._handle()
            except (BrokenPipeError, ConnectionResetError):
                pass  # клієнт закрив з'єднання
            except Exception as error:  # noqa: BLE001 — відповідаємо 500, а не падаємо
                try:
                    self._send_error(error)
                except (BrokenPipeError, ConnectionResetError):
                    pass

        def _handle(self) -> None:
            url = urlsplit(self.path or "/")
            path = url.path
            query = parse_qs(url.query, keep_blank_values=True)

            def param(name: str) -> str | None:
                values = query.get(name)
                return values[0] if values else None

            method = self.command

            static_file = STATIC_FILES.get(path)
            if method in ("GET", "HEAD") and static_file:
                self._serve_static(static_file)
                return

            if path == "/api/users" and method == "GET":
                self._send_json(
                    200, {"users": [u.to_dict() for u in messenger.list_users()]}
                )
                return

            if path == "/api/users" and method == "POST":
                body = self._read_object()
                user = messenger.create_user(_require_string(body, "username"))
                self._send_json(201, {"user": user.to_dict()})
                return

            if path == "/api/conversations" and method == "GET":
                user_id = param("userId")
                if not user_id or not messenger.get_user(user_id):
                    raise MessengerError("Користувача не знайдено.", 404)
                conversations = messenger.list_conversations_for_user(user_id)
                self._send_json(
                    200, {"conversations": [c.to_dict() for c in conversations]}
                )
                return

            if path == "/api/conversations" and method == "POST":
                body = self._read_object()
                participant_ids = body.get("participantIds")
                if not isinstance(participant_ids, list):
                    raise MessengerError(CHECK_FIELDS_MESSAGE)
                if not all(isinstance(item, str) for item in participant_ids):
                    raise MessengerError("Перевірте список учасників.")
                conversation = messenger.create_conversation(
                    _require_string(body, "title"), participant_ids
                )
                self._send_json(201, {"conversation": conversation.to_dict()})
                return

            messages_route = MESSAGES_ROUTE.match(path)
            if messages_route and method in ("GET", "POST"):
                conversation_id = messages_route.group(1)
                if method == "GET":
                    user_id = param("userId")
                    conversation = messenger.get_conversation(conversation_id)
                    if (
                        not user_id
                        or conversation is None
                        or user_id not in conversation.participant_ids
                    ):
                        raise MessengerError("Розмову не знайдено.", 404)
                    messages = messenger.get_messages(conversation_id)
                    self._send_json(200, {"messages": [m.to_dict() for m in messages]})
                    return

                body = self._read_object()
                message = messenger.send_message(
                    conversation_id,
                    _require_string(body, "senderId"),
                    _require_string(body, "text"),
                )
                self._send_json(201, {"message": message.to_dict()})
                return

            if path == "/api/search" and method == "GET":
                user_id = param("userId")
                if not user_id or not messenger.get_user(user_id):
                    raise MessengerError("Користувача не знайдено.", 404)
                messages = messenger.search_messages(user_id, param("q") or "")
                self._send_json(200, {"messages": [m.to_dict() for m in messages]})
                return

            if path.startswith("/api/"):
                self._send_json(404, {"error": "Маршрут не знайдено."})
                return

            self._send_json(404, {"error": "Сторінку не знайдено."})

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = do_HEAD = _dispatch

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            pass  # тихий режим; помилки сервера йдуть через log_error

        def log_error(self, format: str, *args: Any) -> None:  # noqa: A002
            import sys

            print("[messenger]", format % args, file=sys.stderr)

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server
