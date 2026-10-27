"""Время в состоянии контура: статусы и ремонты ABAI — в местном времени."""

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.compensation.constants import ABAI_STATUS_IDLE, STOP_IDLE, STOP_REPAIR
from apps.compensation.services.state import CompensationStateService

NOW_UTC = datetime(2026, 9, 30, 9, 30)  # noqa: DTZ001 — 14:30 по Атырау
NOW_LOCAL = NOW_UTC + timedelta(hours=5)
IDLE_SINCE_LOCAL = datetime(2026, 9, 30, 1, 0)  # noqa: DTZ001
FAR = datetime(3333, 12, 31)  # noqa: DTZ001 — открытый интервал ABAI
# Простой кончился в 12:30 местного — 2 ч назад. Если считать статусы в UTC
# (сейчас 09:30), он выглядел бы ещё идущим: та самая ошибка пояса.
ENDED_SINCE_LOCAL = datetime(2026, 9, 29, 22, 0)  # noqa: DTZ001
ENDED_UNTIL_LOCAL = datetime(2026, 9, 30, 12, 30)  # noqa: DTZ001
REPAIR_START_LOCAL = datetime(2026, 9, 30, 12, 0)  # noqa: DTZ001 — 2,5 ч назад


class _Statuses:
    def __init__(self) -> None:
        self.asked_since: datetime | None = None

    async def list_intervals_by_abai_well_ids(
        self,
        _ids: list,
        *,
        since: datetime,
        until: datetime,
    ) -> list:
        self.asked_since = since
        rows = [
            (1001, IDLE_SINCE_LOCAL, FAR, ABAI_STATUS_IDLE, "Простой", None),
            (
                1003,
                ENDED_SINCE_LOCAL,
                ENDED_UNTIL_LOCAL,
                ABAI_STATUS_IDLE,
                "Простой",
                None,
            ),
        ]
        # Как в запросе: интервалы, пересекающие [since, until).
        return [row for row in rows if row[1] < until and row[2] >= since]


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


def test_statuses_and_repairs_are_local() -> None:
    service = CompensationStateService.__new__(CompensationStateService)
    service.status_repository = statuses = _Statuses()  # type: ignore[assignment]
    service.repair_repository = repairs = _Repairs()  # type: ignore[assignment]
    service.regime_repository = _Regimes()  # type: ignore[assignment]
    service.incident_status_service = _Incidents()  # type: ignore[assignment]
    wells = [
        SimpleNamespace(id=1, abai_id=1001),
        SimpleNamespace(id=2, abai_id=1002),
        SimpleNamespace(id=3, abai_id=1003),
    ]

    state = asyncio.run(service.load(wells, now=NOW_UTC))  # type: ignore[arg-type]

    assert statuses.asked_since == NOW_LOCAL
    assert repairs.asked_now == NOW_LOCAL
    assert state.stops[1].kind == STOP_IDLE
    assert state.stops[1].since == IDLE_SINCE_LOCAL  # без сдвига на +5 ч
    assert state.stops[2].kind == STOP_REPAIR
    assert state.stops[2].since == REPAIR_START_LOCAL
    assert 3 not in state.stops  # простой уже кончился по местному времени
