# ruff: noqa: RUF001
"""AI processor: does the actual repair report match the ПОР plan?

Compares the works planned in the ПОР document (``por_file_id`` PDF, converted
to text) against what the crew actually reported (``RepairSummary`` shift
details). Produces a deviation percentage and a short text verdict.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from langchain.messages import HumanMessage

from apps.repairs.tasks.fill_analytics.ai.base import BaseAIProcessor


@dataclass(slots=True)
class PORConfirmationInput:
    repair_id: int
    por_text: str | None
    report_items: list[str] = field(default_factory=list)


_OUTPUT_SCHEMA_HINT = {
    "deviation_pct": "число 0..100 — процент отклонения отчёта от плана ПОР",
    "verdict": "строка — почему такое отклонение, по-русски, кратко",
}


class PORConfirmationProcessor(BaseAIProcessor[PORConfirmationInput]):
    prompt_version: str = "v1"

    def _build_state(self, item: PORConfirmationInput) -> dict[str, Any]:
        payload = {
            "repair_id": item.repair_id,
            "por_plan_text": item.por_text,
            "report_items": item.report_items,
        }
        prompt = (
            "Ты — инженер технадзора ПРС. Сравни ФАКТИЧЕСКИЙ отчёт бригады "
            "(`report_items`) с планом работ, описанным в документе ПОР "
            "(`por_plan_text`). Оцени, насколько отчёт отклоняется от плана: "
            "какие пункты плана не выполнены/выполнены иначе, какие работы "
            "появились сверх плана.\n\n"
            "Верни СТРОГО валидный JSON — без пояснений и без markdown-обёртки.\n"
            "- deviation_pct: 0 = отчёт полностью соответствует плану ПОР, "
            "100 = полностью не соответствует.\n"
            "- verdict: короткое объяснение причины отклонения по-русски.\n"
            "Если `por_plan_text` пустой — deviation_pct = null и укажи это "
            "в verdict.\n\n"
            "Схема ответа:\n"
            f"{json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False)}\n\n"
            "Входные данные:\n"
            f"{json.dumps(payload, ensure_ascii=False, default=str)}\n"
        )
        return {"messages": [HumanMessage(content=prompt)]}
