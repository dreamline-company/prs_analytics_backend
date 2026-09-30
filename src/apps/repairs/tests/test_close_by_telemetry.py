"""Закрытие ремонта, когда скважина уже работает, а ABAI его не закрыл."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.repairs.tasks.close_by_telemetry.close_by_telemetry import (
    CloseRepairsByTelemetry,
    cits_start,
    sdmo_running,
)

NOW = datetime(2026, 9, 30, 22, 0)  # noqa: DTZ001
START = datetime(2026, 9, 26, 17, 45)  # noqa: DTZ001


def test_cits_needs_two_days() -> None:
    day1 = datetime(2026, 9, 28, 9, 0)  # noqa: DTZ001
    assert cits_start([day1, day1 + timedelta(hours=5)]) is None
    assert cits_start([day1 + timedelta(days=1), day1]) == day1


def test_sdmo_needs_fresh_and_mostly_running() -> None:
    fresh = NOW - timedelta(minutes=10)
    assert sdmo_running(100, 60, fresh, now=NOW)
    assert not sdmo_running(100, 40, fresh, now=NOW)  # крутился меньше половины
    assert not sdmo_running(100, 90, NOW - timedelta(hours=3), now=NOW)  # не на связи
    assert not sdmo_running(0, 0, None, now=NOW)


class _Repairs:
    def __init__(self) -> None:
        self.updated: dict[int, object] = {}

    async def get_list(self, _spec: object) -> list:
        return [SimpleNamespace(id=1, abai_well_id=101, start_time=START)]

    async def update_by_id(self, repair_id: int, data: object) -> None:
        self.updated[repair_id] = data


class _Wells:
    async def list_by_abai_ids(self, _ids: list[int]) -> list:
        return [SimpleNamespace(id=7, abai_id=101, name="VMB_2542")]


class _Telemetry:
    async def list_by_well_id_in_period(
        self,
        _well_id: int,
        *,
        date_time_from: datetime,
    ) -> list:
        rows = [
            (START + timedelta(hours=2), 30.0),  # первые сутки — до бригады
            (datetime(2026, 9, 29, 8, 0), 25.0),  # noqa: DTZ001
            (datetime(2026, 9, 30, 8, 0), 26.0),  # noqa: DTZ001
        ]
        return [
            SimpleNamespace(date_time=moment, qv_liquid=q)
            for moment, q in rows
            if moment >= date_time_from
        ]


class _Stations:
    async def list_by_well_id(self, _well_id: int) -> list:
        return [SimpleNamespace(id=1113)]


class _FcData:
    async def get_rotor_running_stats(
        self,
        _station_id: int,
        **_kw: object,
    ) -> tuple:
        return 700, 500, NOW - timedelta(minutes=5)

    async def get_first_rotor_running(
        self,
        _station_id: int,
        **_kw: object,
    ) -> datetime:
        return datetime(2026, 9, 28, 14, 0)  # noqa: DTZ001


def test_closed_at_earliest_evidence_after_first_day() -> None:
    task = CloseRepairsByTelemetry.__new__(CloseRepairsByTelemetry)
    task.now = NOW
    task.repairs = _Repairs()  # type: ignore[assignment]
    task.wells = _Wells()  # type: ignore[assignment]
    task.telemetry = _Telemetry()  # type: ignore[assignment]
    task.stations = _Stations()  # type: ignore[assignment]
    task.fc_data = _FcData()  # type: ignore[assignment]

    closures = asyncio.run(task.run())

    assert [(c.end_time, c.source) for c in closures] == [
        (datetime(2026, 9, 28, 14, 0), "СДМО"),  # noqa: DTZ001
    ]
    update = task.repairs.updated[1]
    assert update.end_time == datetime(2026, 9, 28, 14, 0)  # noqa: DTZ001
    assert update.closed_by_telemetry_at == NOW


def test_dry_run_changes_nothing() -> None:
    task = CloseRepairsByTelemetry.__new__(CloseRepairsByTelemetry)
    task.now = NOW
    task.repairs = _Repairs()  # type: ignore[assignment]
    task.wells = _Wells()  # type: ignore[assignment]
    task.telemetry = _Telemetry()  # type: ignore[assignment]
    task.stations = _Stations()  # type: ignore[assignment]
    task.fc_data = _FcData()  # type: ignore[assignment]

    assert len(asyncio.run(task.run(dry_run=True))) == 1
    assert task.repairs.updated == {}
