# ruff: noqa: RUF001
"""AI processor: how far does the report diverge from the SPO events?

Compares the crew report (``RepairSummary`` shift details) against the recorded
СПО events (``SPOEvent`` rows — the ground-truth trip log) and produces a
deviation percentage plus a short text verdict.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from langchain.messages import HumanMessage

from apps.repairs.tasks.fill_analytics.ai.base import BaseAIProcessor


@dataclass(slots=True)
class SPOAnalysisInput:
    repair_id: int
    spo_events: list[str] = field(default_factory=list)
    report_items: list[str] = field(default_factory=list)


_OUTPUT_SCHEMA_HINT = {
    "deviation_pct": "число 0..100 — насколько отчёт расходится с событиями СПО",
    "verdict": "строка — в чём именно расхождение, по-русски, кратко",
}


class SPOAnalysisProcessor(BaseAIProcessor[SPOAnalysisInput]):
    prompt_version: str = "v1"

    def _build_state(self, item: SPOAnalysisInput) -> dict[str, Any]:
        payload = {
            "repair_id": item.repair_id,
            "spo_events": item.spo_events,
            "report_items": item.report_items,
        }
        prompt = (
            "Ты — инженер по спуско-подъёмным операциям (СПО). События СПО "
            "(`spo_events`) — это объективный лог операций с прибора. Отчёт "
            "бригады (`report_items`) — то, что записано вручную. Оцени, "
            "насколько отчёт расходится с фактическими событиями СПО: "
            "пропущенные операции, лишние записи, несоответствие "
            "последовательности.\n\n"
            "Верни СТРОГО валидный JSON — без пояснений и без markdown-обёртки.\n"
            "- deviation_pct: 0 = отчёт полностью соответствует событиям СПО, "
            "100 = полностью расходится.\n"
            "- verdict: короткое объяснение причины расхождения по-русски.\n"
            "Если `spo_events` пустой — deviation_pct = null и укажи это в "
            "verdict.\n\n"
            "Схема ответа:\n"
            f"{json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False)}\n\n"
            "Входные данные:\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        )
        return {"messages": [HumanMessage(content=prompt)]}
