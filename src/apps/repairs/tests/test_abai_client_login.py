"""Клиент ABAI: один логин на клиент под конкурентностью и перелогин после 401."""

from __future__ import annotations

import asyncio

import httpx
import pytest

from shared.integrations.abai.api.client import AbaiAsyncClient, AbaiAuthError


class _FakeAbai:
    """Laravel-подобный портал: сессия живёт, пока её не отозвали."""

    def __init__(self) -> None:
        self.valid: set[str] = set()
        self.logins = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path == "/ru/login":
            return httpx.Response(
                200,
                text='<input name="_token" value="tok">',
                headers={"set-cookie": "XSRF-TOKEN=x; Path=/"},
            )
        if path == "/ru/prelogin":
            self.logins += 1
            sid = f"s{self.logins}"
            self.valid.add(sid)
            return httpx.Response(
                302,
                headers={
                    "location": "/ru/home",
                    "set-cookie": f"kmg_ai_session={sid}; Path=/",
                },
            )
        if path == "/ru/home":
            return httpx.Response(200, text="home")
        cookie = req.headers.get("cookie", "")
        if any(f"kmg_ai_session={sid}" in cookie for sid in self.valid):
            return httpx.Response(200, json={"path": path})
        return httpx.Response(401, json={"message": "Unauthenticated."})


def _client(portal: _FakeAbai) -> AbaiAsyncClient:
    client = AbaiAsyncClient(username="u", password="p", domain="d")  # noqa: S106
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url=client.BASE_URL,
        transport=httpx.MockTransport(portal),
        follow_redirects=True,
    )
    return client


def test_concurrent_requests_share_one_login() -> None:
    portal = _FakeAbai()
    client = _client(portal)

    async def scenario() -> list[dict]:
        return await asyncio.gather(
            *(client.search_wells(f"w{i}") for i in range(8)),
        )

    results = asyncio.run(scenario())

    assert portal.logins == 1
    assert len(results) == 8


def test_expired_session_triggers_single_relogin_even_under_concurrency() -> None:
    portal = _FakeAbai()
    client = _client(portal)
    asyncio.run(client.search_wells("warmup"))
    assert portal.logins == 1

    portal.valid.clear()  # ABAI отозвал сессию (TTL)

    async def scenario() -> list[dict]:
        return await asyncio.gather(
            *(client.search_wells(f"w{i}") for i in range(8)),
        )

    results = asyncio.run(scenario())

    assert portal.logins == 2
    assert len(results) == 8


def test_persistent_401_raises_after_one_relogin() -> None:
    portal = _FakeAbai()
    client = _client(portal)
    asyncio.run(client.search_wells("warmup"))

    def always_401(req: httpx.Request) -> httpx.Response:
        if req.url.path.startswith("/ru/api/"):
            return httpx.Response(401)
        return portal(req)

    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url=client.BASE_URL,
        transport=httpx.MockTransport(always_401),
        follow_redirects=True,
    )

    with pytest.raises(AbaiAuthError):
        asyncio.run(client.search_wells("x"))
    assert portal.logins == 2
