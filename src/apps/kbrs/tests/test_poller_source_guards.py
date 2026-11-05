"""Опросчик КБРС: обрезанный ответ не затирает замер, простой догоняется."""

import asyncio
import contextlib
from datetime import datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from apps.kbrs.models.measure import MEASURE_STATUS_OK, MEASURE_STATUS_PARSE_ERROR
from apps.kbrs.tasks.poll_measures import poller as poller_module
from apps.kbrs.tasks.poll_measures.poller import KbrsMeasurePoller, shrunk_or_same

NOW = datetime(2026, 11, 5, 12, 0)  # noqa: DTZ001


def test_shrunk_or_same() -> None:
    assert shrunk_or_same(MEASURE_STATUS_OK, 1000, 400)  # обрезанный ответ
    assert shrunk_or_same(MEASURE_STATUS_PARSE_ERROR, 1000, 400)
    assert shrunk_or_same(MEASURE_STATUS_OK, 1000, 1000)  # не вырос
    assert not shrunk_or_same(MEASURE_STATUS_PARSE_ERROR, 1000, 1000)  # разобрать снова
    assert not shrunk_or_same(MEASURE_STATUS_OK, 1000, 1500)  # вырос
    assert not shrunk_or_same(None, None, 10)  # новый


def _poller() -> KbrsMeasurePoller:
    return KbrsMeasurePoller(
        pool=None,  # type: ignore[arg-type]
        storage=None,  # type: ignore[arg-type]
        poll_interval_seconds=60,
        keepalive_interval_seconds=60,
        window_hours=24,
        refresh_grace_minutes=30,
        page_count=200,
        late_sweep_minutes=30,
        late_sweep_window_hours=72,
    )


@pytest.fixture
def last_fetched(monkeypatch: pytest.MonkeyPatch) -> list:
    value: list = [None]

    @contextlib.asynccontextmanager
    async def session():  # noqa: ANN202
        yield None

    monkeypatch.setattr(poller_module, "session_makers", {"app": session})
    monkeypatch.setattr(
        poller_module,
        "KbrsMeasureRepository",
        lambda _session: SimpleNamespace(
            get_last_fetched_at=AsyncMock(side_effect=lambda: value[0]),
        ),
    )
    monkeypatch.setattr(poller_module, "_local_now", lambda: NOW)
    return value


def test_catch_up_lists_idle_period_day_by_day(last_fetched: list) -> None:
    last_fetched[0] = NOW - timedelta(days=3, hours=5)
    poller = _poller()
    poller._poll_once = AsyncMock()  # noqa: SLF001

    days = asyncio.run(poller.catch_up())

    calls = [c.kwargs for c in poller._poll_once.await_args_list]  # noqa: SLF001
    assert days == len(calls) == 3
    assert calls[0]["date_from"] == last_fetched[0] - timedelta(hours=1)
    assert calls[-1]["date_to"] == NOW - timedelta(hours=24)
    assert all(c["date_to"] - c["date_from"] <= timedelta(days=1) for c in calls)


def test_no_catch_up_after_short_pause(last_fetched: list) -> None:
    last_fetched[0] = NOW - timedelta(hours=2)
    poller = _poller()
    poller._poll_once = AsyncMock()  # noqa: SLF001

    assert asyncio.run(poller.catch_up()) == 0
    poller._poll_once.assert_not_awaited()  # noqa: SLF001
