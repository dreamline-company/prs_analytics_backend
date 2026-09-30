"""R9 в утреннем запуске оценивает вчерашние сутки — по местному времени, не по UTC."""

import asyncio
from datetime import UTC, date, datetime, tzinfo
from types import SimpleNamespace

import pytest

from apps.detectors.load_imbalance.tasks.run_incidents import run_incidents

# Запуск по расписанию 04:10 Атырау (UTC+5) — в UTC это ещё 23:10 прошлых суток.
RUN_AT_UTC = datetime(2026, 9, 29, 23, 10, tzinfo=UTC)


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz: tzinfo | None = None) -> datetime:  # type: ignore[override]
        return RUN_AT_UTC.astimezone(tz) if tz else RUN_AT_UTC.replace(tzinfo=None)


class _Source:
    def __init__(self) -> None:
        self.loaded_until: list[date] = []

    async def get_data_front(self, _station_id: int) -> date:
        return date(2026, 9, 30)

    async def load_daily(self, _station_id: int, _start: date, end: date) -> list:
        self.loaded_until.append(end)
        return []


class _Incidents:
    async def get_active(self, **_kwargs: object) -> None:
        return None


class _Cursors:
    def __init__(self) -> None:
        self.last_event_at: list[datetime] = []

    async def upsert(self, *, last_event_at: datetime, **_kwargs: object) -> None:
        self.last_event_at.append(last_event_at)


def test_morning_run_evaluates_yesterday(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_incidents, "datetime", _FixedDatetime)
    runner = run_incidents.LoadImbalanceIncidentRunner.__new__(
        run_incidents.LoadImbalanceIncidentRunner,
    )
    runner.source = _Source()  # type: ignore[assignment]
    runner.incident_repo = _Incidents()  # type: ignore[assignment]
    runner.cursor_repo = _Cursors()  # type: ignore[assignment]

    # Курсор — полночь после последних оценённых суток: оценено по 28.09.
    cursor_ts = datetime(2026, 9, 29)  # noqa: DTZ001
    asyncio.run(
        runner._run_station(  # noqa: SLF001
            SimpleNamespace(id=2174, well_id=459),  # type: ignore[arg-type]
            cursor_ts=cursor_ts,
        ),
    )

    assert runner.source.loaded_until == [date(2026, 9, 29)]
    assert runner.cursor_repo.last_event_at == [datetime(2026, 9, 30)]  # noqa: DTZ001
