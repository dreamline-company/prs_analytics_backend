"""Скважинный ассистент: реестр tools, промпт, краткие формы данных."""

import asyncio
from datetime import datetime
from types import SimpleNamespace

import pytest

from apps.ai_assistant.ai.well_chat_assistant.prompt import (
    DOSSIER_UNAVAILABLE_NOTE,
    NO_WELL_NOTE,
    WELL_CHAT_ASSISTANT_SYSTEM_PROMPT,
    render_system_prompt,
)
from apps.ai_assistant.ai.well_chat_assistant.tools import (
    WELL_CHAT_ASSISTANT_TOOLS,
    WellContextMissingError,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.incidents import incident_brief
from apps.ai_assistant.ai.well_chat_assistant.tools.status import status_brief
from apps.ai_assistant.ai.well_chat_assistant.tools.telemetry import unwrap_moment

EXPECTED_TOOLS = {
    "get_incidents": {"days"},
    "get_ai_conclusion": set(),
    "get_drive_telemetry": {"hours"},
    "get_rates_history": {"days"},
    "get_abai_status_history": {"days"},
    "list_repairs": {"limit"},
    "get_well_mrp": set(),
    "get_repair_analysis": {"repair_id"},
    "get_compensation_contour": set(),
}


def test_registry_has_thin_tools_with_hidden_config() -> None:
    tools = {tool.name: tool for tool in WELL_CHAT_ASSISTANT_TOOLS}

    assert set(tools) == set(EXPECTED_TOOLS)
    for name, params in EXPECTED_TOOLS.items():
        schema = tools[name].tool_call_schema.model_json_schema()
        # config инжектится рантаймом и модели не виден.
        assert set(schema.get("properties", {})) == params, name


def test_system_prompt_describes_every_tool() -> None:
    for name in EXPECTED_TOOLS:
        assert name in WELL_CHAT_ASSISTANT_SYSTEM_PROMPT


def test_system_prompt_tail_depends_on_well_and_dossier() -> None:
    assert render_system_prompt(None, well_bound=False).endswith(f"{NO_WELL_NOTE}\n")
    assert render_system_prompt(None, well_bound=True).endswith(
        f"{DOSSIER_UNAVAILABLE_NOTE}\n",
    )
    assert "Скважина VMB_0406" in render_system_prompt(
        "Скважина VMB_0406",
        well_bound=True,
    )


def test_tool_without_bound_well_raises() -> None:
    tool = next(t for t in WELL_CHAT_ASSISTANT_TOOLS if t.name == "get_incidents")

    with pytest.raises(WellContextMissingError):
        asyncio.run(tool.ainvoke({}, config={"configurable": {}}))


def _incident(**overrides: object) -> SimpleNamespace:
    base = {
        "id": 7,
        "detector_code": "R2",
        "detector_name_ru": "Обрыв штанг",
        "reason_code": "rod_break",
        "level": "alarm",
        "status": "active",
        "opened_at": datetime(2026, 10, 26, 3, 15),  # noqa: DTZ001
        "last_seen_at": datetime(2026, 10, 26, 9, 0),  # noqa: DTZ001
        "escalated_at": None,
        "normalized_at": None,
        "close_reason": None,
        "payload": {
            "current_ratio": 0.05,
            "base_moment": 210.0,
            "evidence": [{"bucket": "…"}] * 8,
        },
        "verification": SimpleNamespace(
            verdict="false_alarm",
            reason="moment_recovered",
            is_final=True,
        ),
    }
    return SimpleNamespace(**{**base, **overrides})


def test_incident_brief_keeps_key_facts_and_verdict() -> None:
    brief = incident_brief(_incident())  # type: ignore[arg-type]

    assert brief["facts"] == {"current_ratio": 0.05, "base_moment": 210.0}
    assert brief["verification"] == {
        "verdict": "false_alarm",
        "verdict_ru": "ложная тревога",
        "reason": "moment_recovered",
        "is_final": True,
    }


def test_incident_brief_r9_adds_background_k() -> None:
    brief = incident_brief(
        _incident(  # type: ignore[arg-type]
            detector_code="R9",
            payload={"k": 0.25, "branches": ["abs"], "baseline": {"k_p90": 0.06}},
            verification=None,
        ),
    )

    assert brief["facts"] == {"k": 0.25, "branches": ["abs"], "k_background_p90": 0.06}
    assert brief["verification"] is None


def test_status_brief_open_interval_has_no_end() -> None:
    since = datetime(2026, 10, 25, 8, 0)  # noqa: DTZ001
    row = (1, since, datetime(3333, 12, 31), "DWN", "В простое", "Нет подачи")  # noqa: DTZ001

    assert status_brief(row) == {
        "status_code": "DWN",
        "status": "В простое",
        "reason": "Нет подачи",
        "since": since.isoformat(),
        "until": None,
    }


def test_unwrap_moment_only_for_shgn() -> None:
    assert unwrap_moment(64636, 1) == -900
    assert unwrap_moment(1200, 1) == 1200
    assert unwrap_moment(64636, 6) == 64636
    assert unwrap_moment(None, 1) is None
