"""Вложения ABAI: страница входа вместо файла не сохраняется."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from apps.repairs.tasks.fill_analytics.fetchers.abai_dynamogram_fetcher import (
    AbaiDynamogramFetcher,
)
from apps.repairs.tasks.fill_analytics.fetchers.abai_repair_doc_fetcher import (
    AbaiRepairDocFetcher,
)
from apps.wells.tasks.load_dynamograms.refetch_rejected import attachment_id
from shared.integrations.abai.api.client import AbaiAsyncClient, AbaiAuthError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
LOGIN_PAGE = b"<!DOCTYPE html><html><title>KMG AI</title><input name=password>"


class _Portal:
    """Сессия протухает — вложение отвечает редиректом на страницу входа."""

    def __init__(self, *, always_html: bool = False) -> None:
        self.valid: set[str] = set()
        self.logins = 0
        self.always_html = always_html

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
        if self.always_html:
            return httpx.Response(200, content=LOGIN_PAGE)
        if any(f"kmg_ai_session={sid}" in cookie for sid in self.valid):
            return httpx.Response(200, content=PNG)
        return httpx.Response(302, headers={"location": "/ru/login"})


def _client(portal: _Portal) -> AbaiAsyncClient:
    client = AbaiAsyncClient(username="u", password="p", domain="d")  # noqa: S106
    client._client = httpx.AsyncClient(  # noqa: SLF001
        base_url=client.BASE_URL,
        transport=httpx.MockTransport(portal),
        follow_redirects=True,
    )
    return client


def test_expired_session_relogins_instead_of_saving_login_page() -> None:
    portal = _Portal()
    client = _client(portal)

    async def scenario() -> bytes:
        await client.login()
        portal.valid.clear()  # сессия протухла на стороне ABAI
        return await client.fetch_attachment(5)

    assert asyncio.run(scenario()) == PNG
    assert portal.logins == 2


def test_html_after_relogin_raises() -> None:
    client = _client(_Portal(always_html=True))

    with pytest.raises(AbaiAuthError):
        asyncio.run(client.fetch_attachment(5))


def test_dynamogram_fetcher_does_not_store_html() -> None:
    storage = MagicMock(upload_file=AsyncMock())
    file_repo = MagicMock(create=AsyncMock())
    fetcher = AbaiDynamogramFetcher(
        abai_client=MagicMock(fetch_attachment=AsyncMock(return_value=LOGIN_PAGE)),
        storage=storage,
        file_repo=file_repo,
        dynamogram_repo=MagicMock(),
    )

    row = asyncio.run(
        fetcher._download_and_register(  # noqa: SLF001
            SimpleNamespace(id=7, file_name="d.png"),
        ),
    )

    assert row is None
    storage.upload_file.assert_not_awaited()
    file_repo.create.assert_not_awaited()


def test_doc_fetcher_keeps_good_pdf_when_abai_sends_html() -> None:
    existing = SimpleNamespace(por_file_id=1, act_file_id=None, source_hash="h")
    storage = MagicMock(upload_file=AsyncMock())
    fetcher = AbaiRepairDocFetcher(
        abai_client=MagicMock(fetch_attachment=AsyncMock(return_value=LOGIN_PAGE)),
        storage=storage,
        file_repo=MagicMock(),
        repair_doc_repo=MagicMock(get_by_repair_id=AsyncMock(return_value=existing)),
    )
    fetcher._find_pdf = AsyncMock(  # noqa: SLF001
        return_value=SimpleNamespace(id=9, file_name="por.pdf"),
    )

    outcome = asyncio.run(
        fetcher.fetch(SimpleNamespace(id=3, abai_id=30, end_time=None), 100),
    )

    assert outcome.doc is existing
    assert not outcome.changed
    storage.upload_file.assert_not_awaited()


def test_attachment_id_from_s3_key() -> None:
    assert attachment_id("dynamograms/2413180/scan.png") == 2413180
    assert attachment_id("repair_docs/1/x.pdf") is None
