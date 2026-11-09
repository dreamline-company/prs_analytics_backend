"""Динамограммы и способ добычи: у ЭВН, УЭЦН и фонтана их нет — это не недостаток."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.repairs.services.lift_type import dynamogram_applicable, get_lift_type
from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)


@pytest.mark.parametrize(
    ("lift_type", "expected"),
    [
        (None, True),  # тип неизвестен — прежнее поведение
        ("", True),
        ("ШГН", True),
        ("САУ ШГН", True),
        ("ИСУ ШГН", True),
        ("Комбинированный", True),
        ("ЭВН", False),
        ("УЭЦН", False),
        ("Фонтан", False),
    ],
)
def test_dynamogram_applicable_by_lift_type(
    lift_type: str | None,
    expected: bool,  # noqa: FBT001
) -> None:
    assert dynamogram_applicable(lift_type) is expected


class _FundRevisions:
    def __init__(self, rows: dict[int, str | None]) -> None:
        self.rows = rows

    async def get_latest_by_well_ids(self, well_ids: list[int]) -> dict:
        return {
            well_id: SimpleNamespace(lift_type=self.rows[well_id])
            for well_id in well_ids
            if well_id in self.rows
        }


def test_get_lift_type_from_latest_revision() -> None:
    repo = _FundRevisions({5: "ЭВН"})

    assert asyncio.run(get_lift_type(5, repo)) == "ЭВН"  # type: ignore[arg-type]
    assert asyncio.run(get_lift_type(6, repo)) is None  # type: ignore[arg-type]
    assert asyncio.run(get_lift_type(None, repo)) is None  # type: ignore[arg-type]


def _calculator() -> RepairKPICalculator:
    return RepairKPICalculator.__new__(RepairKPICalculator)


def test_dynamogram_kpi_not_applicable_for_evn() -> None:
    kpi = _calculator()._dynamogram_kpi(None, None, lift_type="ЭВН")  # noqa: SLF001

    assert kpi["applicable"] is False
    assert kpi["before_pct"] is None
    assert kpi["after_pct"] is None
    assert "не применима" in kpi["verdict"]
    assert "ЭВН" in kpi["verdict"]


def test_dynamogram_kpi_unchanged_for_rod_pump() -> None:
    kpi = _calculator()._dynamogram_kpi(None, None, lift_type="ШГН")  # noqa: SLF001

    assert "applicable" not in kpi
    assert kpi["verdict"] == "Недостаточно данных для сравнения ДО/ПОСЛЕ."


def _overall_state(*, lift_type: str | None, applicable: bool) -> dict:
    pytest.importorskip("langchain.messages")
    from apps.repairs.tasks.fill_analytics.ai.overall_processor import (  # noqa: PLC0415
        OverallAIProcessor,
        OverallProcessingInput,
    )

    repair = SimpleNamespace(
        id=1,
        abai_id=10,
        start_time=datetime(2026, 10, 1, 8, 0),  # noqa: DTZ001
        end_time=None,
    )
    item = OverallProcessingInput(
        repair=repair,  # type: ignore[arg-type]
        lift_type=lift_type,
        dynamogram_applicable=applicable,
    )
    return OverallAIProcessor.__new__(OverallAIProcessor)._build_state(item)  # noqa: SLF001


def _prompt(state: dict) -> str:
    return state["messages"][0].content


def test_overall_prompt_tells_model_dynamograms_are_not_taken_for_evn() -> None:
    prompt = _prompt(_overall_state(lift_type="ЭВН", applicable=False))

    assert '"dynamogram_applicable": false' in prompt
    assert '"lift_type": "ЭВН"' in prompt
    assert "НЕ указывай их отсутствие" in prompt


def test_overall_prompt_input_has_well_block_for_rod_pump() -> None:
    prompt = _prompt(_overall_state(lift_type="ШГН", applicable=True))
    payload = json.loads(prompt.split("Входные данные:\n", 1)[1])

    assert payload["well"] == {"lift_type": "ШГН", "dynamogram_applicable": True}
