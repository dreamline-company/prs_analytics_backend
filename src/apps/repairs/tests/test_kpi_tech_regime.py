"""KPI «выход на тех. режим».

У незавершённого ремонта не считается; у завершённого берётся медиана замеров за 30
суток после окончания, а не последний замер скважины вообще. План — режим на дату
окончания ремонта.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)


class FakeWells:
    async def get_by_abai_id(self, *, abai_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=abai_id)


class FakeTelemetry:
    def __init__(self, rows: list[tuple[datetime, float | None]] | None = None) -> None:
        self.called = False
        self.rows = rows or []

    async def list_by_well_id_in_period(
        self,
        well_id: int,  # noqa: ARG002
        *,
        date_time_from: datetime | None = None,
        date_time_to: datetime | None = None,
    ) -> list[SimpleNamespace]:
        self.called = True
        return [
            SimpleNamespace(date_time=moment, qv_liquid=value)
            for moment, value in self.rows
            if (date_time_from is None or moment >= date_time_from)
            and (date_time_to is None or moment <= date_time_to)
        ]


def _calculator(telemetry: FakeTelemetry) -> RepairKPICalculator:
    calc = RepairKPICalculator.__new__(RepairKPICalculator)
    calc.well_repo = FakeWells()
    calc.telemetry_repo = telemetry
    return calc


def _repair(end_time: datetime | None) -> SimpleNamespace:
    return SimpleNamespace(
        abai_well_id=1,
        end_time=end_time,
        start_time=datetime(2026, 10, 8),  # noqa: DTZ001
    )


def test_open_repair_has_no_current_value() -> None:
    telemetry = FakeTelemetry()
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(None))) is None  # noqa: SLF001
    assert not telemetry.called


END = datetime(2026, 9, 22)  # noqa: DTZ001


def test_finished_repair_takes_median_of_month_after_end() -> None:
    # Пример NRG_0259: после ремонта добывала немного, потом простой. Последний замер
    # (0.0 спустя полтора месяца) не должен давать «0 и −100%».
    telemetry = FakeTelemetry(
        [
            (END + timedelta(hours=18), 7.0),
            (END + timedelta(days=1), 0.5),
            (END + timedelta(days=2), 2.5),
            (END + timedelta(days=45), 0.0),  # за окном — не учитывается
        ],
    )
    calc = _calculator(telemetry)

    value = asyncio.run(calc._tech_regime_current(_repair(END)))  # noqa: SLF001

    assert value == 2.5
    assert telemetry.called


def test_finished_repair_without_measurements_in_window_has_no_value() -> None:
    telemetry = FakeTelemetry([(END + timedelta(days=45), 5.0)])
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(END))) is None  # noqa: SLF001


def test_null_measurements_are_skipped() -> None:
    telemetry = FakeTelemetry(
        [(END + timedelta(days=1), None), (END + timedelta(days=2), 4.0)],
    )
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(END))) == 4.0  # noqa: SLF001


def test_median_ignores_stop_zeros() -> None:
    # Скважина после ремонта давала ~40, пара остановов с нулём не должна тянуть вниз
    telemetry = FakeTelemetry(
        [
            (END + timedelta(days=1), 40.0),
            (END + timedelta(days=2), 0.0),
            (END + timedelta(days=3), 41.0),
            (END + timedelta(days=4), 0.0),
            (END + timedelta(days=5), 39.0),
            (END + timedelta(days=6), 40.0),
            (END + timedelta(days=7), 42.0),
        ],
    )
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(END))) == 40.0  # noqa: SLF001


class FakeRegimes:
    def __init__(
        self,
        current: dict[int, float | None],
        latest: list[tuple[date, float | None]] | None = None,
    ) -> None:
        self.current = current
        self.latest = latest or []
        self.on_date = None

    async def get_current_by_abai_well_ids(
        self,
        abai_well_ids: list[int],  # noqa: ARG002
        *,
        on_date: date,
        grace_days: int,  # noqa: ARG002
    ) -> dict[int, SimpleNamespace]:
        self.on_date = on_date
        return {k: SimpleNamespace(liquid=v) for k, v in self.current.items()}

    async def list_by_abai_well_id(
        self,
        abai_well_id: int,  # noqa: ARG002
    ) -> list[SimpleNamespace]:
        return [SimpleNamespace(start_date=d, liquid=v) for d, v in self.latest]


def _plan_calculator(regimes: FakeRegimes) -> RepairKPICalculator:
    calc = RepairKPICalculator.__new__(RepairKPICalculator)
    calc.tech_regime_repo = regimes
    return calc


def _plan(regimes: FakeRegimes) -> float | None:
    calc = _plan_calculator(regimes)
    return asyncio.run(calc._tech_regime_plan(_repair(END)))  # noqa: SLF001


def test_plan_is_regime_valid_on_repair_end() -> None:
    regimes = FakeRegimes({1: 45.0}, latest=[(date(2026, 10, 1), 99.0)])

    assert _plan(regimes) == 45.0
    assert regimes.on_date == END.date()


def test_plan_falls_back_to_latest_regime() -> None:
    regimes = FakeRegimes(
        {},
        latest=[(date(2026, 8, 1), 10.0), (date(2026, 9, 1), 30.0)],
    )

    assert _plan(regimes) == 30.0


def test_plan_without_any_regime_is_none() -> None:
    assert _plan(FakeRegimes({})) is None


def test_empty_regime_of_the_month_does_not_erase_plan() -> None:
    # DMB_0055: режим июля 2026 без дебита жидкости перекрывал заданные ранее
    # режимы, и в KPI не было ни плана, ни процента отклонения
    regimes = FakeRegimes(
        {1: None},
        latest=[
            (date(2021, 11, 1), 25.0),
            (date(2026, 7, 1), None),
            (date(2026, 10, 1), 8.0),
        ],
    )

    # ремонт закончился 22.09: режим октября ещё не начался
    assert _plan(regimes) == 25.0


def test_plan_ignores_regimes_that_start_after_repair_when_earlier_exist() -> None:
    regimes = FakeRegimes(
        {},
        latest=[(date(2026, 7, 1), 12.0), (date(2026, 11, 1), 50.0)],
    )

    assert _plan(regimes) == 12.0


def test_plan_takes_later_regime_when_nothing_started_before_repair() -> None:
    regimes = FakeRegimes({}, latest=[(date(2026, 11, 1), 50.0)])

    assert _plan(regimes) == 50.0
