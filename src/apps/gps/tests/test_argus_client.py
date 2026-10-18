"""Клиент Аргуса: формат запроса, ошибки в теле при HTTP 200, повторы, пароль
не попадает в логи."""

import asyncio
import json
import logging

import httpx
import pytest

from shared.integrations.argus.api import (
    ArgusApiError,
    ArgusClient,
    ArgusTransportError,
)
from shared.integrations.argus.api.client import redact

PASSWORD = "s3cret&x"  # noqa: S105 — тестовое значение


def _client(handler: httpx.MockTransport, **kwargs: object) -> ArgusClient:
    return ArgusClient(
        login="API_EMG",
        password=PASSWORD,
        transport=handler,
        retry_backoff=0,
        **kwargs,
    )


def test_get_unit_msgs_sends_svc_and_json_params() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(
            200,
            json=[{"time": 1791954150, "lat": "47.7", "lon": "54.1", "sats": 9}],
        )

    async def scenario() -> list:
        async with _client(httpx.MockTransport(handler)) as client:
            return await client.get_unit_msgs(243, start=100, end=200)

    messages = asyncio.run(scenario())

    assert [(m.time, m.lat, m.sats) for m in messages] == [(1791954150, 47.7, 9)]
    query = seen[0].url.params
    assert query["login"] == "API_EMG"
    assert query["pass"] == PASSWORD
    assert query["svc"] == "get_unit_msgs"
    params = json.loads(query["params"])
    assert params["id_unit"] == 243
    assert (params["from"], params["to"]) == (100, 200)
    assert params["fields"].startswith("time,lat,lon")


def test_error_in_body_raises_api_error_without_retry() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(200, json={"error": 9, "data_error": "Unit not exists"})

    async def scenario() -> None:
        async with _client(httpx.MockTransport(handler)) as client:
            await client.get_unit_msgs(1, start=0, end=1)

    with pytest.raises(ArgusApiError) as exc_info:
        asyncio.run(scenario())
    assert exc_info.value.code == 9
    assert calls == 1


def test_server_errors_are_retried_then_raise_transport_error() -> None:
    calls = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(502)

    async def scenario() -> None:
        async with _client(httpx.MockTransport(handler), max_retries=2) as client:
            await client.get_units()

    with pytest.raises(ArgusTransportError):
        asyncio.run(scenario())
    assert calls == 3


def test_empty_track_is_empty_list() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async def scenario() -> list:
        async with _client(httpx.MockTransport(handler)) as client:
            return await client.get_unit_msgs(1, start=0, end=1)

    assert asyncio.run(scenario()) == []


def test_password_is_redacted_in_httpx_log(caplog: pytest.LogCaptureFixture) -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[])

    async def scenario() -> None:
        async with _client(httpx.MockTransport(handler)) as client:
            await client.get_units(full=False)

    with caplog.at_level(logging.INFO, logger="httpx"):
        asyncio.run(scenario())

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert "HTTP Request" in text
    assert "s3cret" not in text
    assert "pass=***" in text


def test_redact() -> None:
    assert redact("https://h/sdk/?login=A&pass=p%40ss&svc=x") == (
        "https://h/sdk/?login=A&pass=***&svc=x"
    )
