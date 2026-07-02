"""Builds LangGraph agents for the analytics AI processors.

Each processor gets its own agent so prompts/tools/checkpoints can diverge.
Prompt text and tools stay empty here — the project owner fills them in.
"""

from langchain.agents import create_agent
from langchain_openai import ChatOpenAI
from langgraph.graph.state import CompiledStateGraph

from core.settings import get_settings

settings = get_settings()


def _chat_model() -> ChatOpenAI:
    return ChatOpenAI(
        base_url=settings.LLM_BASE_URL,
        api_key=settings.LLM_API_KEY,
        model=settings.LLM_MODEL_NAME,
    )


def build_dynamogram_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())


def build_spo_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())


def build_overall_agent() -> CompiledStateGraph:
    return create_agent(_chat_model())
