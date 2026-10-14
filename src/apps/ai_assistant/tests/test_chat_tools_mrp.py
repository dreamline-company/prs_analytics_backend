"""Инструмент МРП для ИИ-чата: подключён к агенту и описан для модели."""

from apps.ai_assistant.ai.chat_assistant.agent import CHAT_ASSISTANT_SYSTEM_PROMPT
from apps.ai_assistant.ai.chat_assistant.tools import (
    CHAT_ASSISTANT_TOOLS,
    get_well_mrp,
)


def test_mrp_tool_is_registered() -> None:
    assert get_well_mrp in CHAT_ASSISTANT_TOOLS


def test_mrp_tool_is_described_for_the_model() -> None:
    assert get_well_mrp.name == "get_well_mrp"
    description = get_well_mrp.description
    assert "МРП" in description
    assert "interpretation" in description


def test_system_prompt_mentions_mrp_tool() -> None:
    assert "get_well_mrp" in CHAT_ASSISTANT_SYSTEM_PROMPT
