"""Потери контура: величина по факту ABAI, окно до остановки, состояние."""

import asyncio
from datetime import date, datetime
from types import SimpleNamespace

from apps.compensation.constants import STOP_IDLE, STOP_REPAIR
from apps.compensation.services.losses import (
    SOURCE_FACT,
    SOURCE_PLAN,
    LossValue,
    loss_value,
    loss_window,
)
from apps.compensation.services.pool_data import DailyOil, implied_density
from apps.compensation.services.state import (
    CompensationStateService,
    WellsState,
    apply_losses,
)

SINCE = datetime(2026, 10, 20, 9, 30)  # noqa: DTZ001
FAR = datetime(3333, 12, 31)  # noqa: DTZ001


def test_window_is_ten_full_days_before_stop() -> None:
    days = loss_window(SINCE)

    assert days[0] == datetime(2026, 10, 19)  # noqa: DTZ001
    assert days[-1] == datetime(2026, 10, 10)  # noqa: DTZ001
    assert len(days) == 10


def test_loss_value_fact_first_then_plan() -> None:
    assert loss_value(3.1, 1.5) == (3.1, SOURCE_FACT)
    assert loss_value(0.05, 1.5) == (1.5, SOURCE_PLAN)  # факт почти 0
    assert loss_value(None, 1.5) == (1.5, SOURCE_PLAN)
    assert loss_value(None, None) == (None, None)


def test_daily_oil_from_intervals() -> None:
    oil = DailyOil(
        liquid={7: [(datetime(2026, 10, 1), FAR, 40.0)]},  # noqa: DTZ001
        water_cut={
            7: [
                (datetime(2026, 10, 1), datetime(2026, 10, 15), 90.0),  # noqa: DTZ001
                (datetime(2026, 10, 15), FAR, 95.0),  # noqa: DTZ001
            ],
        },
        density={7: 0.9},
    )

    assert round(oil.on(7, datetime(2026, 10, 10)), 3) == 3.6  # noqa: DTZ001
    assert round(oil.on(7, datetime(2026, 10, 16)), 3) == 1.8  # noqa: DTZ001
    assert oil.on(8, datetime(2026, 10, 10)) is None  # noqa: DTZ001


def test_implied_density_from_tech_mode() -> None:
    assert round(implied_density(40, 1.07, 97, None), 4) == 0.8917
    assert implied_density(40, 1.0, 97, 0.88) == 0.88
    assert implied_density(40, 1.0, 100, None) is None


def test_apply_losses_fills_state() -> None:
    state = WellsState()
    apply_losses(
        state,
        [
            LossValue(1, STOP_IDLE, "Привод остановлен", SINCE, 3.1, SOURCE_FACT, 77),
            LossValue(2, STOP_REPAIR, "ПРС", SINCE, None, None, None),
        ],
        on_date=date(2026, 11, 4),
    )

    assert state.stops[1].kind == STOP_IDLE
    assert state.losses == {1: (3.1, date(2026, 11, 4))}
    assert state.loss_sources == {1: SOURCE_FACT}
    assert state.gzu == {1: 77, 2: None}
    assert 2 in state.stops  # стоит, но потерю не оценить


class _Losses:
    async def list_by_well_ids(self, _ids: list[int]) -> list:
        return [
            SimpleNamespace(
                well_id=1,
                stop_kind=STOP_REPAIR,
                stop_text="Смена насоса",
                stop_since=SINCE,
                loss=2.5,
                loss_source=SOURCE_PLAN,
                gzu_abai_id=5,
                calculated_at=datetime(2026, 11, 4, 10, 15),  # noqa: DTZ001
            ),
        ]


class _Incidents:
    async def get_for_wells(self, ids: list[int]) -> dict:
        return {i: SimpleNamespace(level="alarm" if i == 2 else None) for i in ids}


def test_state_service_reads_loss_table() -> None:
    service = CompensationStateService.__new__(CompensationStateService)
    service.loss_repository = _Losses()  # type: ignore[assignment]
    service.incident_status_service = _Incidents()  # type: ignore[assignment]
    wells = [SimpleNamespace(id=1), SimpleNamespace(id=2)]

    state = asyncio.run(service.load(wells, now=datetime(2026, 11, 4, 6, 0)))  # type: ignore[arg-type]  # noqa: DTZ001

    assert state.stops[1].kind == STOP_REPAIR
    assert state.losses[1] == (2.5, date(2026, 11, 4))
    assert state.loss_sources[1] == SOURCE_PLAN
    assert state.alarms == {2}
