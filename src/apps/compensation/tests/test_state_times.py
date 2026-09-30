"""Время в состоянии контура: статусы ABAI — в UTC, ремонты ABAI — местные."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.compensation.constants import ABAI_STATUS_IDLE, STOP_IDLE, STOP_REPAIR
from apps.compensation.services.state import CompensationStateService

NOW_UTC = datetime(2026, 9, 30, 9, 30)  # noqa: DTZ001 — 14:30 по Атырау
IDLE_SINCE_UTC = datetime(2026, 9, 29, 20, 0)  # noqa: DTZ001
REPAIR_START_LOCAL = datetime(2026, 9, 30, 12, 0)  # noqa: DTZ001 — 2,5 ч назад


class _Statuses:
    async def list_intervals_by_abai_well_ids(self, *_a: object, **_kw: object) -> list:
        far = NOW_UTC + timedelta(days=1)
        return [(1001, IDLE_SINCE_UTC, far, ABAI_STATUS_IDLE, "Простой", None)]


class _Repairs:
    def __init__(self) -> None:
        self.asked_now: datetime | None = None

    async def list_current_by_abai_well_ids(self, _ids: list, *, now: datetime) -> dict:
        self.asked_now = now
        if now < REPAIR_START_LOCAL:  # как start_time <= now в запросе
            return {}
        return {
            1002: SimpleNamespace(
                repair_type_name_ru="Смена насоса",
                start_time=REPAIR_START_LOCAL,
            ),
        }


class _Regimes:
    async def get_current_by_abai_well_ids(self, *_a: object, **_kw: object) -> dict:
        return {}


class _Incidents:
    async def get_for_wells(self, ids: list[int]) -> dict:
        return {i: SimpleNamespace(level=None) for i in ids}


def test_repair_is_local_and_idle_is_utc() -> None:
    service = CompensationStateService.__new__(CompensationStateService)
    service.status_repository = _Statuses()  # type: ignore[assignment]
    service.repair_repository = repairs = _Repairs()  # type: ignore[assignment]
    service.regime_repository = _Regimes()  # type: ignore[assignment]
    service.incident_status_service = _Incidents()  # type: ignore[assignment]
    wells = [SimpleNamespace(id=1, abai_id=1001), SimpleNamespace(id=2, abai_id=1002)]

    state = asyncio.run(service.load(wells, now=NOW_UTC))  # type: ignore[arg-type]

    assert repairs.asked_now == NOW_UTC + timedelta(hours=5)
    assert state.stops[1].kind == STOP_IDLE
    assert state.stops[1].since == IDLE_SINCE_UTC + timedelta(hours=5)
    assert state.stops[2].kind == STOP_REPAIR
    assert state.stops[2].since == REPAIR_START_LOCAL
