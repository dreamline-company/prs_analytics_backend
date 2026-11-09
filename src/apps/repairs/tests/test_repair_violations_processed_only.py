"""Нарушения ТБ в KPI ремонта и в общем ИИ-выводе — только обработанные экраны CM.

Страница аналитики ремонта показывает только обработанные экраны. Если KPI и ИИ-вывод
считают все срабатывания камеры, на одном экране получается «нарушений нет» в панели и
«63 нарушения» в заключении.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)

START = datetime(2026, 9, 20, 8, 0)  # noqa: DTZ001
END = datetime(2026, 9, 22, 18, 0)  # noqa: DTZ001


class _Links:
    async def get_by_repair_id(self, _repair_id: int) -> SimpleNamespace:
        return SimpleNamespace(brigade_id=1)


class _Brigades:
    async def get_by_id(self, brigade_id: int) -> SimpleNamespace:
        return SimpleNamespace(id=brigade_id, name="Бригада ПРС №12")

    async def map_ngdu_abai_ids(self, ids: list[int]) -> dict[int, int]:
        return dict.fromkeys(ids, 12)


class _CMBrigades:
    async def list_by_names(self, numbers: list[str]) -> list[SimpleNamespace]:  # noqa: ARG002
        return [SimpleNamespace(id=7, name="12", ngdu_id=1)]


class _Screens:
    def __init__(self) -> None:
        self.processed_only: list[bool] = []

    async def list_by_brigade_ids_in_range(
        self,
        brigade_ids: list[int],  # noqa: ARG002
        *,
        start_time: datetime,  # noqa: ARG002
        end_time: datetime,  # noqa: ARG002
        processed_only: bool = False,
    ) -> list[SimpleNamespace]:
        self.processed_only.append(processed_only)
        return [
            SimpleNamespace(
                timestamp=START,
                description="Нет каски",
                is_processed=True,
            ),
        ]


def _repair() -> SimpleNamespace:
    return SimpleNamespace(id=100, start_time=START, end_time=END)


def _wire(target: object) -> _Screens:
    screens = _Screens()
    target.repair_brigade_repo = _Links()  # type: ignore[attr-defined]
    target.unique_brigade_repo = _Brigades()  # type: ignore[attr-defined]
    target.cm_brigade_repo = _CMBrigades()  # type: ignore[attr-defined]
    target.cm_brigade_error_screen_repo = screens  # type: ignore[attr-defined]
    return screens


def test_kpi_counts_only_processed_screens() -> None:
    calc = RepairKPICalculator.__new__(RepairKPICalculator)
    screens = _wire(calc)

    count = asyncio.run(calc._count_violations(_repair()))  # noqa: SLF001

    assert count == 1
    assert screens.processed_only == [True]


def test_overall_ai_gets_only_processed_violations() -> None:
    # координатор тянет разбор PDF-динамограмм; без библиотеки модуль не импортируется
    pytest.importorskip("pypdfium2")
    from apps.repairs.tasks.fill_analytics.ai.coordinator import (  # noqa: PLC0415
        AICoordinator,
    )

    coordinator = AICoordinator.__new__(AICoordinator)
    screens = _wire(coordinator)

    violations = asyncio.run(
        coordinator._fetch_repair_violations(_repair()),  # noqa: SLF001
    )

    assert [v.description for v in violations] == ["Нет каски"]
    assert screens.processed_only == [True]
