"""LLM-summary заключения: объяснить улики эпизода человеческим языком.

Единственное место конвейера заключений, где участвует LLM. Все числа и
рекомендации приходят из кода (catalog, facts) — модель только связывает
готовые физические факты; упавший вызов даёт заключению status=failed,
подметальщик перезапустит.
"""

from dataclasses import dataclass
from typing import Any

from langchain.agents import create_agent
from langchain.messages import HumanMessage
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from apps.detectors.conclusion.facts import facts_for
from apps.repairs.tasks.fill_analytics.ai.base import BaseAIProcessor
from core.settings import get_settings

settings = get_settings()

# v2: вместо сырых улик правила (k, k_p90, пороги) — физические факты из
# facts.py: операторам нужна картина насоса, а не механика детектора.
PROMPT_VERSION = "v2"


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
        facts = "\n".join(f"- {fact}" for fact in facts_for(item))
        prompt = (
            "Ты — технолог по механизированной добыче нефти и пишешь для "
            "оператора на промысле. Объясни в 2–3 предложениях по-русски, что "
            "физически происходит с насосом и штангами на скважине "
            f"{item.well_name or ''}.\n\n"
            "Правила ответа:\n"
            "- Только текст, без markdown, без списков, без приветствий.\n"
            "- Не давай рекомендаций — они формируются отдельно.\n"
            "- Используй только факты ниже, числа бери из них как есть; "
            "чего нет в фактах — не упоминай.\n"
            "- Причину называй вероятной («похоже», «вероятно»), а не "
            "установленным фактом.\n"
            "- Если в фактах есть дата последних данных станции — назови её.\n"
            "- Пиши о физике: ход вверх и вниз, нагрузка на штанги, утечка, "
            "заполнение насоса, дебит. Нельзя: латиница, обозначения и "
            "названия переменных, слова «коэффициент», «порог», «правило», "
            "«детекция», «медиана», «перцентиль», «алгоритм».\n\n"
            f"Вероятная причина: {item.cause}\n"
            f"Факты:\n{facts}"
        )
        return {"messages": [HumanMessage(content=prompt)]}

    def _parse_output(self, final_state: Any) -> dict[str, Any]:  # noqa: ANN401
        base = super()._parse_output(final_state)
        raw = base.get("raw")
        return {"text": raw.strip() if isinstance(raw, str) else None}
