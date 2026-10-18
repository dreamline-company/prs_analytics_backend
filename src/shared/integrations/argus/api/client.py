"""Клиент SDK «Аргуса» — GPS-мониторинг транспорта ЭМГ (Wialon-подобный).

Все вызовы — ``GET <base>?login=…&pass=…&svc=<метод>&params=<JSON>``; ошибка
приходит в теле при HTTP 200. Описание ответов — ``docs/argus_api.md``.

Логин и пароль идут в строке запроса, поэтому URL целиком никуда не пишется:
логгер ``httpx`` (на INFO он печатает каждый запрос, а celery-воркер поднимает
корневой логгер до INFO) получает фильтр, вырезающий пароль, а ошибки клиента
URL не содержат.

``get_units_msgs`` (несколько машин разом) намеренно не реализован: ответ —
JSON внутри строки, и при пустом треке у любой машины он битый.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from http import HTTPStatus
from typing import Any, Self

import httpx

from shared.integrations.abai.api.client import ConnectToTransport, parse_connect_to
from shared.integrations.argus.api.dtos import ArgusMessageDto, ArgusUnitDto
from shared.integrations.argus.api.exceptions import (
    ArgusApiError,
    ArgusTransportError,
)

log = logging.getLogger("argus")

# Поля трека, которые нужны загрузчику: с ``fields`` ответ в ~12 раз меньше.
TRACK_FIELDS = ("time", "lat", "lon", "speed", "course", "height", "sats", "params")

_SECRET_RE = re.compile(r"(?<=[?&]pass=)[^&\s\"']*")


def redact(value: str) -> str:
    """Строка с паролем из query (``pass=…``), заменённым на ``***``."""
    return _SECRET_RE.sub("***", value)


class _RedactPasswordFilter(logging.Filter):
    """Вырезает ``pass=`` из записей ``httpx`` ("HTTP Request: GET <url> …")."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = redact(record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                redact(str(arg)) if isinstance(arg, str | httpx.URL) else arg
                for arg in record.args
            )
        return True


def _install_redaction() -> None:
    httpx_logger = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactPasswordFilter) for f in httpx_logger.filters):
        httpx_logger.addFilter(_RedactPasswordFilter())


_install_redaction()


class ArgusClient:
    BASE_URL = "https://admin.argus.emg.kmg.kz/sdk/"

    def __init__(  # noqa: PLR0913
        self,
        *,
        login: str,
        password: str,
        base_url: str | None = None,
        connect_to: str | list[str] | dict | None = None,
        timeout: float = 30.0,
        max_retries: int = 2,
        retry_backoff: float = 2.0,
        verify: bool = False,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._login = login
        self._password = password
        self._url = base_url or self.BASE_URL
        self._max_retries = max_retries
        self._retry_backoff = retry_backoff
        # Сертификат admin.argus.emg.kmg.kz внутренний — не проверяем, как у
        # Exchange и УТО. connect_to — на случай, если DNS контейнера не знает
        # хост: "admin.argus.emg.kmg.kz:443:10.32.10.113:443".
        if transport is None:
            transport = ConnectToTransport(parse_connect_to(connect_to), verify=verify)
        self._client = httpx.AsyncClient(timeout=timeout, transport=transport)

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def get_units(self, *, full: bool = True) -> list[ArgusUnitDto]:
        """Справочник машин. ``full`` — с карточкой ``cf``, ``lmsg`` и датчиками
        (~8 МБ на 713 машин); без него — только id/имя/трекер (~1,4 МБ)."""
        payload = await self._call("get_units", {} if full else {"short_details": 1})
        if not isinstance(payload, list):
            msg = f"get_units: expected list, got {type(payload).__name__}"
            raise ArgusApiError(msg)
        units = [ArgusUnitDto.from_payload(i) for i in payload if isinstance(i, dict)]
        return [unit for unit in units if unit is not None]

    async def get_unit_msgs(
        self,
        unit_id: int,
        *,
        start: int,
        end: int,
        fields: tuple[str, ...] = TRACK_FIELDS,
    ) -> list[ArgusMessageDto]:
        """Трек машины ``unit_id`` (id объекта, не IMEI) за ``[start, end]``,
        unix-секунды UTC. Пустой период — пустой список."""
        params: dict[str, Any] = {"id_unit": unit_id, "from": start, "to": end}
        if fields:
            params["fields"] = ",".join(fields)
        payload = await self._call("get_unit_msgs", params)
        if not isinstance(payload, list):
            msg = f"get_unit_msgs: expected list, got {type(payload).__name__}"
            raise ArgusApiError(msg)
        messages = [
            ArgusMessageDto.from_payload(i) for i in payload if isinstance(i, dict)
        ]
        return [message for message in messages if message is not None]

    async def _call(self, svc: str, params: dict[str, Any]) -> Any:  # noqa: ANN401
        query = {
            "login": self._login,
            "pass": self._password,
            "svc": svc,
            "params": json.dumps(params, separators=(",", ":")),
        }
        attempt = 0
        while True:
            try:
                response = await self._client.get(self._url, params=query)
            except httpx.TransportError as exc:
                error: Exception = exc
                reason = type(exc).__name__
            else:
                if response.status_code < HTTPStatus.INTERNAL_SERVER_ERROR:
                    return self._parse(svc, response)
                reason = f"HTTP {response.status_code}"
                error = ArgusTransportError(f"{svc}: {reason}")
            if attempt >= self._max_retries:
                msg = f"{svc}: {reason}"
                raise ArgusTransportError(msg) from error
            attempt += 1
            delay = self._retry_backoff**attempt
            log.warning(
                "Argus %s failed (%s), retry %s/%s in %.0fs",
                svc,
                reason,
                attempt,
                self._max_retries,
                delay,
            )
            await asyncio.sleep(delay)

    @staticmethod
    def _parse(svc: str, response: httpx.Response) -> Any:  # noqa: ANN401
        if response.status_code != HTTPStatus.OK:
            msg = f"{svc}: HTTP {response.status_code}"
            raise ArgusApiError(msg)
        try:
            payload = response.json()
        except ValueError as exc:
            msg = f"{svc}: response is not JSON"
            raise ArgusApiError(msg) from exc
        if isinstance(payload, dict) and "error" in payload:
            code = payload.get("error")
            msg = f"{svc}: error {code} ({payload.get('data_error')})"
            raise ArgusApiError(msg, code=code if isinstance(code, int) else None)
        return payload
