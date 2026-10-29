"""Скважинный чат: обрезка истории треда и ошибки вопроса без разрыва WS."""

import asyncio
from types import SimpleNamespace
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from apps.ai_assistant.ai.well_chat_assistant.agent import trim_history
from apps.ai_assistant.ai.well_chat_assistant.tools.incidents import counts_brief
from apps.ai_assistant.routers.ws.v1 import well_assistant_chat as ws_module
from apps.ai_assistant.use_cases.ask_well_chat_assistant import (
    WellChatModelRejectedError,
)


def _turn(n: int, size: int) -> list:
    """Ход: вопрос, вызов tool, ответ tool размером ``size`` символов, ответ."""
    call = {"name": "get_incidents", "args": {}, "id": f"call-{n}"}
    return [
        HumanMessage(f"вопрос {n}", id=f"h{n}"),
        AIMessage("", tool_calls=[call], id=f"a{n}"),
        ToolMessage("х" * size, tool_call_id=f"call-{n}", id=f"t{n}"),
        AIMessage(f"ответ {n}", id=f"r{n}"),
    ]


def test_short_thread_is_not_trimmed() -> None:
    assert trim_history([*_turn(1, 100), *_turn(2, 100)], max_tokens=7000) is None


def test_trim_drops_old_turns_and_keeps_current_whole() -> None:
    messages = [*_turn(1, 6000), *_turn(2, 6000), *_turn(3, 6000)]
    messages = messages[:-1]  # текущий ход: ответа модели ещё нет

    kept = trim_history(messages, max_tokens=7000)

    assert kept is not None
    # Ход 1 не влез, ход 2 — целиком, текущий ход 3 — целиком.
    assert [m.id for m in kept] == ["h2", "a2", "t2", "r2", "h3", "a3", "t3"]
    # Ответ tool не остался без своего вызова.
    tool_ids = {m.tool_call_id for m in kept if isinstance(m, ToolMessage)}
    call_ids = {c["id"] for m in kept if isinstance(m, AIMessage) for c in m.tool_calls}
    assert tool_ids <= call_ids


def test_trim_keeps_oversized_current_turn() -> None:
    messages = [*_turn(1, 100), *_turn(2, 40000)][:-1]

    kept = trim_history(messages, max_tokens=7000)

    assert kept is not None
    assert [m.id for m in kept] == ["h2", "a2", "t2"]


class _Ws:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


class _UseCase:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def execute(self, command: Any) -> None:  # noqa: ANN401, ARG002
        raise self.error


def _ask(error: Exception) -> tuple[_Ws, ws_module.WellChatSession, str]:
    ws = _Ws()
    session = ws_module.WellChatSession(chat_id="c1")
    session.bind_well(6086)
    thread_before = session.thread_id
    request = SimpleNamespace(message="как скважина?")
    asyncio.run(
        ws_module._handle_message(ws, session, request, _UseCase(error)),  # type: ignore[arg-type]  # noqa: SLF001
    )
    return ws, session, thread_before or ""


def test_rejected_request_resets_thread_and_answers() -> None:
    ws, session, thread_before = _ask(WellChatModelRejectedError("context length"))

    assert ws.sent == [
        {
            "type": "message",
            "chat_id": "c1",
            "text": ws_module.THREAD_RESET_TEXT,
            "format": "markdown",
        },
    ]
    assert session.thread_id != thread_before
    assert session.well_id == 6086


def test_other_errors_keep_thread_and_connection() -> None:
    ws, session, thread_before = _ask(RuntimeError("db down"))

    assert ws.sent == [
        {
            "type": "message",
            "chat_id": "c1",
            "text": ws_module.ANSWER_FAILED_TEXT,
            "format": "markdown",
        },
    ]
    assert session.thread_id == thread_before


def test_counts_brief_is_exact_not_page_size() -> None:
    assert counts_brief({"R9": (2, 0), "R2": (34, 21)}) == {
        "total": 36,
        "false_alarms": 21,
        "by_detector": {
            "R2": {"total": 34, "false_alarms": 21},
            "R9": {"total": 2, "false_alarms": 0},
        },
    }
