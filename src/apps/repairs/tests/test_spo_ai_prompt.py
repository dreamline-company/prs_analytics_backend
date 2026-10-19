"""Промпт разбора СПО: русский язык и единая схема ответа.

Раньше промпт на английском просил «Return findings as JSON» без схемы, и ИИ
отдавал английский JSON с произвольными именами полей у каждого замера.
"""

import json
from datetime import datetime
from types import SimpleNamespace

from apps.repairs.tasks.fill_analytics.ai.spo_processor import (
    _OUTPUT_SCHEMA_HINT,
    SPOAIProcessor,
)

CHART = (
    "timestamp,datetime,hook_weight_t,h2s_mg_m3,ch4_percent\n"
    "1,2026-10-01T18:38:50,0.5,,\n"
)


def _prompt() -> str:
    item = SimpleNamespace(
        spo=SimpleNamespace(id=1919, snapshot_time=datetime(2026, 9, 29, 14, 23)),  # noqa: DTZ001
        chart_text=CHART,
        notes_text='{"channels": 3}',
    )
    processor = SPOAIProcessor(agent=None)  # type: ignore[arg-type]
    state = processor._build_state(item)  # type: ignore[arg-type]  # noqa: SLF001
    return state["messages"][0].content


def test_prompt_asks_for_russian_strict_json() -> None:
    prompt = _prompt()

    assert "ПО-РУССКИ" in prompt
    assert "СТРОГО валидный JSON" in prompt
    assert "без markdown-обёртки" in prompt
    assert "Return findings as JSON" not in prompt


def test_prompt_contains_response_schema() -> None:
    prompt = _prompt()

    assert json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False) in prompt
    for key in ("summary", "hook_weight", "events", "gas", "attention_points"):
        assert key in _OUTPUT_SCHEMA_HINT


def test_prompt_keeps_measurement_data_and_gas_rules() -> None:
    prompt = _prompt()

    assert "spo_id=1919" in prompt
    assert "chart.csv:" in prompt
    assert "notes.json:" in prompt
    assert "Gas sensors, computed from the full measurement" in prompt
    assert "вес на крюке за газ не принимай" in prompt


def test_prompt_version_bumped_for_reprocessing() -> None:
    # v1 — английский JSON без схемы; координатор пересчитывает всё не-v2.
    assert SPOAIProcessor.prompt_version == "v2"
