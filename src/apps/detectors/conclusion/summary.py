"""LLM-summary заключения: объяснить улики эпизода человеческим языком.

Единственное место конвейера заключений, где участвует LLM. Все числа и
рекомендации приходят из кода (catalog) — модель только пересказывает улики;
упавший вызов даёт заключению status=failed, подметальщик перезапустит.
"""

import json
from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from apps.repairs.tasks.fill_analytics.ai.base import BaseAIProcessor
from core.settings import get_settings

settings = get_settings()

PROMPT_VERSION = "v1"

# Не скармливать LLM всю историю улик: последних корзин/суток достаточно для
# пересказа, а промпт остаётся коротким.
_EVIDENCE_TAIL = 8


@dataclass(slots=True)
class ConclusionSummaryInput:
    cause: str
    detector_code: str
    level: str
    well_name: str | None
    opened_at: str
    last_seen_at: str
    payload: dict | None
    oil_rate: float | None
    liquid_rate: float | None
    water_cut: float | None
    plan_oil_rate: float | None
    plan_liquid_rate: float | None


def build_summary_agent() -> CompiledStateGraph:
    return create_agent(
        ChatOpenAI(
            base_url=settings.LLM_BASE_URL,
            api_key=settings.LLM_API_KEY,
            model=settings.LLM_MODEL_NAME,
            extra_body={"reasoning_effort": "none"},
        ),
    )


class ConclusionSummaryProcessor(BaseAIProcessor[ConclusionSummaryInput]):
    prompt_version = PROMPT_VERSION

    def _build_state(self, item: ConclusionSummaryInput) -> dict[str, Any]:
        payload = dict(item.payload or {})
        evidence = payload.get("evidence")
        if isinstance(evidence, list) and len(evidence) > _EVIDENCE_TAIL:
            payload["evidence"] = evidence[-_EVIDENCE_TAIL:]

        data = {
            "правило": item.cause,
            "уровень": item.level,
            "скважина": item.well_name,
            "эпизод_открыт": item.opened_at,
            "последнее_подтверждение": item.last_seen_at,
            "улики_правила": payload,
            "паспорт": {
                "дебит_нефти_факт": item.oil_rate,
                "дебит_жидкости_факт": item.liquid_rate,
                "обводнённость_процент": item.water_cut,
                "дебит_нефти_план": item.plan_oil_rate,
                "дебит_жидкости_план": item.plan_liquid_rate,
            },
        }
        prompt = (
            "Ты — технолог по механизированной добыче нефти. По данным ниже "
            "объясни в 2–4 предложениях по-русски, что происходит на скважине "
            "и почему сработало правило детекции.\n\n"
            "Правила ответа:\n"
            "- Только текст, без markdown, без списков, без приветствий.\n"
            "- Не давай рекомендаций — они формируются отдельно.\n"
            "- Не выдумывай числа и факты: используй только данные из входа; "
            "чего нет во входе — не упоминай.\n"
            "- Пиши в терминологии домена (момент, заполнение насоса, "
            "полезная нагрузка, дебит).\n\n"
            f"Данные:\n{json.dumps(data, ensure_ascii=False, default=str)}"
        )
        return {"messages": [HumanMessage(content=prompt)]}

    def _parse_output(self, final_state: Any) -> dict[str, Any]:  # noqa: ANN401
        base = super()._parse_output(final_state)
        raw = base.get("raw")
        return {"text": raw.strip() if isinstance(raw, str) else None}
