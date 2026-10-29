"""Метки рассуждения модели не доходят до клиента и не копятся в истории."""

import asyncio

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langgraph.checkpoint.memory import InMemorySaver

from apps.ai_assistant.ai.middleware import strip_model_thinking
from apps.ai_assistant.dto.responses.well_chat_assistant import (
    WellChatAssistantResponseDTO,
)
from shared.ai.llm.thinking import strip_thinking

# Ответ скважинного чата 29.10.2026 — пустой блок рассуждения перед текстом.
LEAKED = (
    "<|channel>thought\n<channel|>Скважина стоит, так как находится в консервации "
    "с 01.09.2026 (статус ABAI: CNS)."
)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        (
            LEAKED,
            "Скважина стоит, так как находится в консервации с 01.09.2026 "
            "(статус ABAI: CNS).",
        ),
        (
            "<|channel>thought\n* Well: EVN\n* Drive stopped\n<channel|>**Стоит.**",
            "**Стоит.**",
        ),
        ("<think>\nрассуждаю\n</think>\n\n**Ответ**", "**Ответ**"),
        ("Текст <channel|>без пары", "Текст без пары"),
        ("**Вывод.**\n- факт 1\n- факт 2", "**Вывод.**\n- факт 1\n- факт 2"),
    ],
)
def test_strip_thinking(raw: str, clean: str) -> None:
    assert strip_thinking(raw) == clean


def test_agent_answer_and_history_have_no_thinking() -> None:
    model = GenericFakeChatModel(messages=iter([AIMessage(content=LEAKED)]))
    agent = create_agent(
        model=model,
        tools=[],
        middleware=[strip_model_thinking],
        checkpointer=InMemorySaver(),
    )
    config = {"configurable": {"thread_id": "t1"}}

    result = asyncio.run(
        agent.ainvoke({"messages": [HumanMessage("почему скв стоит?")]}, config),
    )

    answer = result["messages"][-1]
    assert answer.content.startswith("Скважина стоит")
    stored = agent.get_state(config).values["messages"]
    assert [m.type for m in stored] == ["human", "ai"]
    assert "<|channel>" not in stored[-1].content


def test_well_chat_message_is_markdown() -> None:
    frame = WellChatAssistantResponseDTO(chat_id="c1", text="**Стоит.**")
    assert frame.model_dump(mode="json") == {
        "type": "message",
        "chat_id": "c1",
        "text": "**Стоит.**",
        "format": "markdown",
    }
