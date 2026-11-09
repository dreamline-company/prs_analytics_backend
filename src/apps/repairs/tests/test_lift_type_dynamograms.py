"""Динамограммы и способ добычи: у ЭВН, УЭЦН и фонтана их нет — это не недостаток.

Способ берётся из ABAI (периоды well_expl) отдельно до и после ремонта.
"""

from __future__ import annotations

import asyncio
import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from apps.repairs.services.lift_type import (
    RepairLiftTypes,
    dynamogram_applicable,
    get_repair_lift_types,
)
from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)


@pytest.mark.parametrize(
    ("lift_type", "expected"),
    [
        (None, True),  # способ неизвестен — прежнее поведение
        ("", True),
        ("ШГН", True),
        ("САУ ШГН", True),
        ("ИСУ ШГН", True),
        ("Комбинированный (ШГН+ЭЦН)", True),
        ("Фонтан(ШГН)", True),
        ("Не указано", True),
        ("Новый способ", True),  # нет в справочнике — прежнее поведение
        ("ЭВН", False),
        ("УЭЦН", False),
        ("ЭЦВ", False),
        ("Фонтан", False),
        ("Винтовой насос", False),
        ("УЭВШН", False),
        ("НКТ с пакером", False),
        ("Отсутствует", False),
        (" эвн ", False),
    ],
)
def test_dynamogram_applicable_by_lift_type(
    lift_type: str | None,
    expected: bool,  # noqa: FBT001
) -> None:
    assert dynamogram_applicable(lift_type) is expected


class _WellExpl:
    def __init__(self, periods: list[tuple[date | None, str | None]]) -> None:
        self.periods = periods

    async def list_named_periods_by_abai_well_id(
        self,
        abai_well_id: int,  # noqa: ARG002
    ) -> list[tuple[date | None, str | None]]:
        return self.periods


def _repair(
    start: datetime,
    end: datetime | None,
    abai_well_id: int | None = 1,
) -> SimpleNamespace:
    return SimpleNamespace(abai_well_id=abai_well_id, start_time=start, end_time=end)


START = datetime(2026, 10, 10, 8, 0)  # noqa: DTZ001
END = datetime(2026, 10, 15, 18, 0)  # noqa: DTZ001
TODAY = date(2026, 11, 9)


def _lifts(
    periods: list[tuple[date | None, str | None]],
    repair: SimpleNamespace,
) -> RepairLiftTypes:
    return asyncio.run(
        get_repair_lift_types(repair, _WellExpl(periods), today=TODAY),  # type: ignore[arg-type]
    )


def test_lift_types_same_method_on_both_sides() -> None:
    lifts = _lifts([(None, "ШГН"), (date(2024, 3, 1), "ЭВН")], _repair(START, END))

    assert lifts == RepairLiftTypes(before="ЭВН", after="ЭВН")


@pytest.mark.parametrize(
    "change_day",
    [
        date(2026, 10, 10),
        date(2026, 10, 12),
        date(2026, 10, 15),
        date(2026, 10, 20),
        date(2026, 10, 22),
    ],
)
def test_lift_types_change_during_repair(change_day: date) -> None:
    # Перевод ЭВН → ШГН в ремонте: новый период открыт в день начала, внутри
    # ремонта, в день окончания или в течение недели после — «до» ЭВН, «после» ШГН.
    lifts = _lifts(
        [(date(2023, 1, 1), "ЭВН"), (change_day, "ШГН")],
        _repair(START, END),
    )

    assert lifts == RepairLiftTypes(before="ЭВН", after="ШГН")


def test_change_long_after_repair_is_not_its_result() -> None:
    lifts = _lifts(
        [(date(2023, 1, 1), "ЭВН"), (date(2026, 10, 23), "ШГН")],
        _repair(START, END),
    )

    assert lifts == RepairLiftTypes(before="ЭВН", after="ЭВН")


def test_open_repair_after_is_today() -> None:
    lifts = _lifts(
        [(date(2023, 1, 1), "ЭВН"), (date(2026, 11, 1), "ШГН")],
        _repair(START, None),
    )

    assert lifts == RepairLiftTypes(before="ЭВН", after="ШГН")


def test_lift_types_unknown_without_periods_or_well() -> None:
    assert _lifts([], _repair(START, END)) == RepairLiftTypes()
    assert _lifts([(None, "ЭВН")], _repair(START, END, None)) == RepairLiftTypes()


def _calculator() -> RepairKPICalculator:
    return RepairKPICalculator.__new__(RepairKPICalculator)


def _dynamogram(score: float) -> SimpleNamespace:
    return SimpleNamespace(result={"parsed": {"score": score}})


EVN = RepairLiftTypes(before="ЭВН", after="ЭВН")


def test_dynamogram_kpi_not_applicable_for_evn() -> None:
    kpi = _calculator()._dynamogram_kpi(None, None, lift_types=EVN)  # noqa: SLF001

    assert kpi["applicable"] is False
    assert kpi["before_pct"] is None
    assert kpi["after_pct"] is None
    assert kpi["verdict"] == (
        "Динамограмма не применима: способ добычи «ЭВН», динамограмма не снимается."
    )


def test_dynamogram_kpi_unchanged_for_rod_pump() -> None:
    kpi = _calculator()._dynamogram_kpi(  # noqa: SLF001
        None,
        None,
        lift_types=RepairLiftTypes(before="ШГН", after="ШГН"),
    )

    assert kpi["applicable"] is True
    assert kpi["verdict"] == "Недостаточно данных для сравнения ДО/ПОСЛЕ."


def test_dynamogram_kpi_lift_changed_to_rod_pump() -> None:
    kpi = _calculator()._dynamogram_kpi(  # noqa: SLF001
        None,
        _dynamogram(80),  # type: ignore[arg-type]
        lift_types=RepairLiftTypes(before="ЭВН", after="ШГН"),
    )

    assert kpi["applicable"] is False
    assert kpi["after_pct"] == 80
    assert kpi["direction"] is None
    assert kpi["verdict"] == (
        "Сравнение ДО/ПОСЛЕ не применимо: до ремонта способ добычи «ЭВН», "
        "динамограмма не снималась. Оценка после ремонта — 80.0%."
    )


def test_dynamogram_kpi_uses_dynamograms_even_if_lift_says_evn() -> None:
    kpi = _calculator()._dynamogram_kpi(  # noqa: SLF001
        _dynamogram(40),  # type: ignore[arg-type]
        _dynamogram(70),  # type: ignore[arg-type]
        lift_types=EVN,
    )

    assert kpi["applicable"] is True
    assert kpi["direction"] == "better"


def _overall_state(lifts: RepairLiftTypes) -> dict:
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
        lift_type_before=lifts.before,
        lift_type_after=lifts.after,
        dynamogram_before_applicable=lifts.dynamogram_before_applicable,
        dynamogram_after_applicable=lifts.dynamogram_after_applicable,
    )
    return OverallAIProcessor.__new__(OverallAIProcessor)._build_state(item)  # noqa: SLF001


def _prompt(state: dict) -> str:
    return state["messages"][0].content


def _payload(prompt: str) -> dict:
    return json.loads(prompt.split("Входные данные:\n", 1)[1])


def test_overall_prompt_tells_model_dynamograms_are_not_taken_for_evn() -> None:
    prompt = _prompt(_overall_state(EVN))

    assert "НЕ указывай её отсутствие" in prompt
    assert _payload(prompt)["well"] == {
        "lift_type_before": "ЭВН",
        "lift_type_after": "ЭВН",
        "dynamogram_before_applicable": False,
        "dynamogram_after_applicable": False,
    }


def test_overall_prompt_input_for_lift_change() -> None:
    prompt = _prompt(_overall_state(RepairLiftTypes(before="ЭВН", after="ШГН")))

    assert _payload(prompt)["well"] == {
        "lift_type_before": "ЭВН",
        "lift_type_after": "ШГН",
        "dynamogram_before_applicable": False,
        "dynamogram_after_applicable": True,
    }
