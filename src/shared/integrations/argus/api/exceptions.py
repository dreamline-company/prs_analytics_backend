class ArgusError(Exception):
    """Базовая ошибка клиента «Аргуса»."""


class ArgusApiError(ArgusError):
    """Аргус ответил ошибкой.

    HTTP у SDK всегда 200, ошибка приходит в теле: ``{"error": 9,
    "data_error": "Unit not exists"}``. Сюда же — не-200 от прокси и ответ не
    той формы. Повторять запрос смысла нет.
    """

    def __init__(self, message: str, *, code: int | None = None) -> None:
        super().__init__(message)
        self.code = code


class ArgusTransportError(ArgusError):
    """Аргус недоступен: соединение, таймаут — после всех повторов."""
