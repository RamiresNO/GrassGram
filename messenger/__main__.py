from __future__ import annotations

import os
import signal
import sys
from pathlib import Path

from .server import create_messenger_server
from .service import MessengerService
from .storage import JsonRepository, SqliteRepository

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _build_repository():
    backend = os.environ.get("STORAGE", "json").lower()
    if backend == "json":
        return JsonRepository(
            os.environ.get("DATA_FILE_PATH") or Path.cwd() / "data" / "messages.json"
        )
    if backend == "sqlite":
        return SqliteRepository(
            os.environ.get("DATABASE_PATH") or Path.cwd() / "data" / "messenger.db"
        )
    raise ValueError("STORAGE must be either 'json' or 'sqlite'.")


def main() -> int:
    try:
        messenger = MessengerService.open(_build_repository())
        port_text = os.environ.get("PORT", "3000")
        try:
            port = int(port_text)
        except ValueError:
            port = 0
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be a valid port number.")
        host = os.environ.get("HOST", "127.0.0.1")
        server = create_messenger_server(messenger, PROJECT_ROOT / "public", host, port)
    except Exception as error:  # noqa: BLE001
        print(f"Messenger could not start: {error}", file=sys.stderr)
        return 1

    def _stop(signum, frame):  # SIGTERM -> такий самий чистий вихід, як Ctrl+C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _stop)

    print(f"Study Hall Messenger is running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
