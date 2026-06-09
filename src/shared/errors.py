import json
from typing import Any

from fastapi import HTTPException, WebSocketException
from starlette import status


class AppError(Exception):
    message: str = "Application error."
    code: str = "app_error"

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.details = details or {}

        super().__init__(self.message)

    def __str__(self) -> str:
        if not self.details:
            return self.message

        return f"{self.message} | details={self.details!r}"


class HttpError(HTTPException):
    message: str = "Application error."
    code: str = "app_error"
    status_code: int = 500

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.status_code = status_code or self.status_code
        self.details = details or {}

        super().__init__(
            status_code=status_code,
            detail=message + " Details: " + str(details),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }


class WSError(WebSocketException):
    message: str = "WebSocket error."
    code: str = "ws_error"
    close_code: int = status.WS_1011_INTERNAL_ERROR

    def __init__(
        self,
        message: str | None = None,
        *,
        code: str | None = None,
        close_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.code = code or self.code
        self.close_code = close_code or self.close_code
        self.details = details or {}

        super().__init__(
            code=self.close_code,
            reason=self.message,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            },
        }

    def to_str(self) -> str:
        return str(self.to_dict())

    def to_jsons(self) -> str:
        return json.dumps(self.to_dict())
