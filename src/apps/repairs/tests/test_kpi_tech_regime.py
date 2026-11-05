"""KPI «выход на тех. режим»: у незавершённого ремонта значение не считается."""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)


class FakeWells:
    async def get_by_abai_id(self, *, abai_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=abai_id)


class FakeTelemetry:
    def __init__(self) -> None:
        self.called = False

    async def list_by_well_id_in_period(
        self,
        well_id: int,  # noqa: ARG002
        *,
        date_time_from: datetime | None = None,  # noqa: ARG002
    ) -> list[SimpleNamespace]:
        self.called = True
        return [SimpleNamespace(qv_liquid=0.4)]


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


def test_finished_repair_uses_telemetry_after_end() -> None:
    telemetry = FakeTelemetry()
    calc = _calculator(telemetry)

    value = asyncio.run(
        calc._tech_regime_current(_repair(datetime(2026, 10, 12))),  # noqa: SLF001, DTZ001
    )

    assert value == 0.4
    assert telemetry.called
