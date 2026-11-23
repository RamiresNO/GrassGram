from __future__ import annotations


class MessengerError(Exception):
    """Помилка предметної області, яку можна безпечно показати користувачу.

    ``status_code`` — HTTP-статус, з яким API відповість клієнту.
    """

    def __init__(self, message: str, status_code: int = 400) -> None:
        super().__init__(message)
        self.message = message
        self.status_code = status_code
