# ruff: noqa: RUF001
"""LangGraph processor for the whole-repair analysis.

Consumes the per-item results (dynamogram-before, dynamogram-after, SPOs) and
brigade violation records for the repair interval, and produces one
aggregated verdict for the repair with a strict JSON schema.
"""

import json
from dataclasses import dataclass, field
from typing import Any

from langchain.messages import HumanMessage

from apps.repairs.models.repair import Repair

from .base import BaseAIProcessor


@dataclass(slots=True)
class OverallViolationDTO:
    timestamp: str
    description: str


@dataclass(slots=True)
class OverallProcessingInput:
    repair: Repair
    dynamogram_before_result: dict | None = None
    dynamogram_after_result: dict | None = None
    spo_results: list[dict] = field(default_factory=list)
    violations: list[OverallViolationDTO] = field(default_factory=list)


_OUTPUT_SCHEMA_HINT = {
    "score": "integer 0..100 — общая оценка ремонта",
    "good_points": ["строки — что сработало хорошо"],
    "risks_and_issues": ["строки — риски и замечания"],
    "attention_points": ["строки — на что обратить внимание"],
    "recommendations": ["строки — рекомендации"],
}


class OverallAIProcessor(BaseAIProcessor[OverallProcessingInput]):
    prompt_version: str = "v2"

    def _build_state(self, item: OverallProcessingInput) -> dict[str, Any]:
        summary = {
            "repair_id": item.repair.id,
            "repair_abai_id": item.repair.abai_id,
            "start_time": item.repair.start_time.isoformat(),
            "end_time": (
                item.repair.end_time.isoformat()
                if item.repair.end_time is not None
                else None
            ),
            "dynamogram_before_result": item.dynamogram_before_result,
            "dynamogram_after_result": item.dynamogram_after_result,
            "spo_results": item.spo_results,
            "brigade_violations": {
                "count": len(item.violations),
                "items": [
                    {"timestamp": v.timestamp, "description": v.description}
                    for v in item.violations
                ],
            },
        }
        prompt = (
            "Ты — аналитик по ремонту скважин. На основании входных данных "
            "оцени ремонт и верни СТРОГО валидный JSON — без пояснений, без "
            "текста вокруг, без markdown-обёртки.\n\n"
            "Обязательные поля (все обязательны, списки могут быть пустыми):\n"
            "- score: целое число 0..100 (100 = идеальный ремонт).\n"
            "- good_points: список строк — что сработало хорошо "
            "(например: 'рост добычи после ремонта', 'ремонт завершён быстро').\n"
            "- risks_and_issues: список строк — риски и замечания "
            "(например: 'Динамограмма до ремонта — неполное заполнение насоса', "
            "'1 нарушение ТБ во время ремонта').\n"
            "- attention_points: список строк — на что обратить внимание "
            "(сомнительные моменты, требующие проверки инженером).\n"
            "- recommendations: список строк — что порекомендовать бригаде / "
            "инженеру.\n\n"
            "Правила:\n"
            "- Если из данных нельзя сделать вывод по какому-то пункту — не "
            "выдумывай, оставь список пустым.\n"
            "- Если во входе `brigade_violations.count > 0`, обязательно упомяни "
            "это в risks_and_issues с точным числом.\n"
            "- Пиши по-русски, коротко и по делу. Без вводных фраз.\n\n"
            "Схема ответа (пример):\n"
            f"{json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False)}\n\n"
            "Входные данные:\n"
            f"{json.dumps(summary, ensure_ascii=False, default=str)}\n"
        )
        return {"messages": [HumanMessage(content=prompt)]}
