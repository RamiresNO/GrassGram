from __future__ import annotations

import json
import os
import uuid
from pathlib import Path

from ..models import MessengerData, empty_messenger_data
from .validation import parse_messenger_data


class JsonRepository:
    """Зберігає всі дані одним JSON-файлом (формат сумісний з TypeScript-версією)."""

    def __init__(self, file_path: str | os.PathLike[str]) -> None:
        self._file_path = Path(file_path)

    def load(self) -> MessengerData:
        try:
            contents = self._file_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return empty_messenger_data()

        try:
            raw = json.loads(contents)
        except ValueError as error:
            raise ValueError(
                f"Could not parse messenger data at {self._file_path}."
            ) from error

        data = parse_messenger_data(raw)
        if data is None:
            raise ValueError(
                f"Messenger data at {self._file_path} has an invalid format."
            )
        return data

    def save(self, data: MessengerData) -> None:
        """Атомарний запис: тимчасовий файл у тій самій папці, потім ``os.replace``."""
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path = self._file_path.with_name(
            f"{self._file_path.name}.{uuid.uuid4()}.tmp"
        )
        try:
            with open(temporary_path, "x", encoding="utf-8") as handle:
                json.dump(data.to_dict(), handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self._file_path)
        except BaseException:
            try:
                temporary_path.unlink()
            except FileNotFoundError:
                pass
            raise
