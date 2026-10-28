"""Builds LangGraph agents for the analytics AI processors.

Each processor gets its own agent so prompts/tools/checkpoints can diverge.
Prompt text and tools stay empty here — the project owner fills them in.
"""

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from core.settings import get_settings

settings = get_settings()


# Разбор динамограммы держит её замок на время вызова: без таймаута зависший
# вызов (обрыв связи без RST) держал бы проход по фонду до лимита celery, а
# аналитику ремонта — на замке. Без повтора: сервер модели дорабатывает
# брошенный по таймауту запрос, и мгновенный повтор только удваивает очередь
# перегруженного сервера (28.10.2026 так все вызовы ушли в таймаут); упавший
# разбор и так повторится через FAILED_RETRY_AFTER.
DYNAMOGRAM_LLM_TIMEOUT_SEC = 120
DYNAMOGRAM_LLM_MAX_RETRIES = 0


def _chat_model(**kwargs: object) -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL_NAME,
        extra_body={
            "reasoning_effort": "none",
        },
        **kwargs,
    )


def build_dynamogram_agent() -> CompiledStateGraph:
    return create_agent(
        _chat_model(
            timeout=DYNAMOGRAM_LLM_TIMEOUT_SEC,
            max_retries=DYNAMOGRAM_LLM_MAX_RETRIES,
        ),
    )


def build_spo_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())


def build_overall_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())


def build_kpi_por_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())


def build_kpi_spo_analysis_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())
