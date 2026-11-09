"""KPI «выход на тех. режим».

У незавершённого ремонта не считается; у завершённого берётся среднее за первые
7 суток после окончания, а не последний замер скважины вообще.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
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


def test_finished_repair_averages_first_week_after_end() -> None:
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

    assert value == 3.33
    assert telemetry.called


def test_finished_repair_without_measurements_in_window_has_no_value() -> None:
    telemetry = FakeTelemetry([(END + timedelta(days=30), 5.0)])
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(END))) is None  # noqa: SLF001


def test_null_measurements_are_skipped() -> None:
    telemetry = FakeTelemetry(
        [(END + timedelta(days=1), None), (END + timedelta(days=2), 4.0)],
    )
    calc = _calculator(telemetry)

    assert asyncio.run(calc._tech_regime_current(_repair(END))) == 4.0  # noqa: SLF001
