"""Скважинный чат из карточки уведомления: привязка, блок в промпте, tool."""

import asyncio
from datetime import datetime, timedelta
from typing import Any

import pytest
from langchain.agents import create_agent
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver

from apps.ai_assistant.ai.well_chat_assistant import notification as block_module
from apps.ai_assistant.ai.well_chat_assistant.agent import (
    WellChatContext,
    get_well_agent_config,
    well_system_prompt,
)
from apps.ai_assistant.ai.well_chat_assistant.notification import (
    BLOCK_LIMIT,
    NotificationBlockProvider,
    render_notification_block,
)
from apps.ai_assistant.ai.well_chat_assistant.prompt import render_system_prompt
from apps.ai_assistant.ai.well_chat_assistant.tools import notifications as tool_module
from apps.ai_assistant.dto.commands.ask_well_chat_assistant import (
    AskWellChatAssistantCommand,
)
from apps.ai_assistant.routers.ws.v1 import well_assistant_chat as ws_module
from apps.ai_assistant.use_cases.ask_well_chat_assistant import (
    AskWellChatAssistantUseCase,
)
from apps.notifications.dto.internal.notification import (
    NotificationFactDTO,
    NotificationRowDTO,
)
from apps.notifications.dto.responses.notification import (
    NotificationCardDTO,
    NotificationChainItemDTO,
    NotificationWellDTO,
)
from shared.ai.llm.messages import HumanMessageDTO

NOW = datetime(2026, 11, 22, 9, 15)  # noqa: DTZ001 — местное наивное


def _chain_item(notification_id: int, minutes: int, severity: str = "warning") -> Any:  # noqa: ANN401
    at = NOW + timedelta(minutes=minutes)
    return NotificationChainItemDTO(
        id=notification_id,
        event_code="r2.load_drop",
        severity=severity,
        title_ru=f"VMB_2051: событие {notification_id}",
        event_at=at,
        created_at=at,
        viewed_at=None,
    )


def _card(*, chain: list | None = None, well_id: int = 812, **overrides: Any) -> Any:  # noqa: ANN401
    data: dict[str, Any] = {
        "id": 1042,
        "event_code": "r2.escalated",
        "severity": "alarm",
        "title_ru": "VMB_2051: возможный обрыв штанг",
        "body_ru": "Момент не вернулся к 75 % обычного за 4 часа.",
        "event_at": NOW - timedelta(minutes=5),
        "created_at": NOW,
        "viewed_at": None,
        "source_type": "detector_incident",
        "source_id": 5531,
        "well": NotificationWellDTO(
            id=well_id,
            name="VMB_2051",
            ngdu_id=5,
            ngdu_name="Кайнармунайгаз",
            exploitation="evn",
        ),
        "payload": {},
        "facts": [
            NotificationFactDTO(label_ru="Начало", value_ru="22.11 05:10"),
            NotificationFactDTO(label_ru="Момент", value_ru="42 % базы"),
        ],
        "chain": chain if chain is not None else [],
    }
    data.update(overrides)
    return NotificationCardDTO(**data)


def test_block_has_event_facts_and_marks_current_in_chain() -> None:
    chain = [_chain_item(1001, -240), _chain_item(1042, 0, "alarm")]
    block = render_notification_block(_card(chain=chain))
    assert "«VMB_2051: возможный обрыв штанг» — авария" in block
    assert "Событие: 22.11.2026 09:10; записано: 22.11.2026 09:15" in block
    assert "Начало — 22.11 05:10; Момент — 42 % базы" in block
    assert "эпизод детектора, id 5531" in block
    assert "VMB_2051: событие 1042 ← это уведомление" in block
    assert len(block) <= BLOCK_LIMIT


def test_block_is_capped_and_keeps_current_in_long_chain() -> None:
    chain = [_chain_item(1042, 0, "alarm")] + [
        _chain_item(2000 + i, 10 * (i + 1)) for i in range(10)
    ]
    long_facts = [
        NotificationFactDTO(label_ru=f"Факт {i}", value_ru="х" * 200) for i in range(20)
    ]
    block = render_notification_block(
        _card(chain=chain, facts=long_facts, body_ru="т" * 2000),
    )
    assert len(block) <= BLOCK_LIMIT
    assert "← это уведомление" in block or block.endswith("…")


def test_prompt_adds_block_only_for_bound_well() -> None:
    with_block = render_system_prompt("ДОСЬЕ", well_bound=True, notification="БЛОК")
    assert with_block.endswith("ДОСЬЕ\n\nПОЛЬЗОВАТЕЛЬ ОТКРЫЛ УВЕДОМЛЕНИЕ\nБЛОК\n")
    header = "\nПОЛЬЗОВАТЕЛЬ ОТКРЫЛ УВЕДОМЛЕНИЕ\n"
    unbound = render_system_prompt(None, well_bound=False, notification="БЛОК")
    assert header not in unbound
    assert "БЛОК" not in unbound
    assert header not in render_system_prompt("ДОСЬЕ", well_bound=True)


class _Agent:
    def __init__(self) -> None:
        self.contexts: list[WellChatContext] = []

    async def ainvoke(self, *, input: Any, config: Any, context: Any) -> Any:  # noqa: A002, ANN401, ARG002
        self.contexts.append(context)
        return {"messages": [AIMessage("ответ")]}


def _ask(notification_id: int | None) -> tuple[_Agent, list[tuple[int, int]]]:
    agent = _Agent()
    calls: list[tuple[int, int]] = []

    async def dossier(_: int) -> str:
        return "ДОСЬЕ"

    async def block(nid: int, well_id: int) -> str:
        calls.append((nid, well_id))
        return "БЛОК"

    use_case = AskWellChatAssistantUseCase(
        agent=agent,  # type: ignore[arg-type]
        config_factory=lambda thread_id, well_id: {},  # type: ignore[arg-type, return-value]  # noqa: ARG005
        dossier_provider=dossier,
        notification_provider=block,
    )
    asyncio.run(
        use_case.execute(
            AskWellChatAssistantCommand(
                chat_id="c1",
                thread_id="t1",
                well_id=812,
                notification_id=notification_id,
                message=HumanMessageDTO(content="Расскажи, что случилось и почему"),
            ),
        ),
    )
    return agent, calls


def test_use_case_puts_notification_block_into_context() -> None:
    agent, calls = _ask(1042)
    assert calls == [(1042, 812)]
    assert agent.contexts[0].notification == "БЛОК"
    assert agent.contexts[0].dossier == "ДОСЬЕ"


def test_old_protocol_without_notification_id_unchanged() -> None:
    agent, calls = _ask(None)
    assert calls == []
    assert agent.contexts[0].notification is None


class _Ws:
    def __init__(self) -> None:
        self.sent: list[dict] = []

    async def send_json(self, data: dict) -> None:
        self.sent.append(data)


def _bind(payload: dict) -> tuple[_Ws, ws_module.WellChatSession]:
    ws = _Ws()
    session = ws_module.WellChatSession(chat_id="c1")
    request = ws_module._parse_payload(payload)  # noqa: SLF001
    asyncio.run(ws_module._handle_set_well(ws, session, request))  # type: ignore[arg-type]  # noqa: SLF001
    return ws, session


def test_bind_with_notification_id_is_remembered_and_echoed() -> None:
    ws, session = _bind({"chat_id": "c1", "well_id": 812, "notification_id": 1042})
    assert session.notification_id == 1042
    assert ws.sent == [
        {"type": "well_set", "chat_id": "c1", "well_id": 812, "notification_id": 1042},
    ]
    thread = session.thread_id
    session.reset_thread()
    assert session.thread_id != thread
    assert session.notification_id == 1042


def test_bind_without_notification_id_works_as_before() -> None:
    ws, session = _bind({"chat_id": "c1", "well_id": 812})
    assert session.notification_id is None
    assert ws.sent[0]["well_id"] == 812
    assert ws.sent[0]["notification_id"] is None
    # Перепривязка без уведомления сбрасывает прежнее.
    session.bind_well(812, 1042)
    session.bind_well(900)
    assert session.notification_id is None


class _CardUseCase:
    card: Any = None

    def __init__(self, **_: Any) -> None:  # noqa: ANN401
        pass

    async def execute(self, _: int) -> Any:  # noqa: ANN401
        return _CardUseCase.card


@pytest.mark.parametrize(
    ("card_well_id", "expect_block"),
    [(812, True), (900, False)],
)
def test_provider_skips_notification_of_another_well(
    monkeypatch: pytest.MonkeyPatch,
    card_well_id: int,
    *,
    expect_block: bool,
) -> None:
    monkeypatch.setattr(block_module, "GetNotificationCardUseCase", _CardUseCase)
    _CardUseCase.card = _card(well_id=card_well_id)
    block = asyncio.run(NotificationBlockProvider().get(1042, 812))
    assert (block is not None) is expect_block


def test_provider_missing_notification_gives_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(block_module, "GetNotificationCardUseCase", _CardUseCase)
    _CardUseCase.card = None
    assert asyncio.run(NotificationBlockProvider().get(1, 812)) is None


class _Rows:
    filters: list[Any] = []  # noqa: RUF012

    def __init__(self, _: Any) -> None:  # noqa: ANN401
        pass

    async def list_rows(self, flt: Any) -> list[NotificationRowDTO]:  # noqa: ANN401
        _Rows.filters.append(flt)
        return [
            NotificationRowDTO(
                id=i,
                event_code="r2.escalated",
                severity="alarm",
                title_ru=f"#{i}",
                body_ru="б" * 500,
                well_id=812,
                abai_ngdu_id=12,
                source_type="detector_incident",
                source_id=1,
                payload={},
                event_at=NOW,
                created_at=NOW,
                viewed_at=NOW if i % 2 else None,
            )
            for i in range(25, 0, -1)
        ][: flt.limit]


class _Session:
    async def __aenter__(self) -> "_Session":  # noqa: PYI034
        return self

    async def __aexit__(self, *_: object) -> None:
        return None


def test_list_well_notifications_tool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(tool_module, "NotificationRepository", _Rows)
    monkeypatch.setattr(tool_module, "session_makers", {"app": _Session})
    monkeypatch.setattr(tool_module, "local_now", lambda: NOW)
    _Rows.filters = []
    result = asyncio.run(
        tool_module.list_well_notifications.ainvoke(
            {"days": 500},
            config={"configurable": {"well_id": 812}},
        ),
    )
    assert result["days"] == 90
    assert result["has_more"] is True
    assert len(result["notifications"]) == 20
    first = result["notifications"][0]
    assert first["severity_ru"] == "авария"
    assert first["viewed"] is True
    assert len(first["body"]) <= 200
    flt = _Rows.filters[0]
    assert (flt.well_id, flt.created_from) == (812, NOW - timedelta(days=90))


class _RecordingModel(GenericFakeChatModel):
    seen: list[list[BaseMessage]] = []  # noqa: RUF012

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        _RecordingModel.seen.append(list(messages))
        return super()._generate(messages, *args, **kwargs)


def test_block_goes_to_system_prompt_not_history() -> None:
    _RecordingModel.seen = []
    agent = create_agent(
        model=_RecordingModel(messages=iter([AIMessage("ответ")])),
        tools=[],
        middleware=[well_system_prompt],
        context_schema=WellChatContext,
        checkpointer=InMemorySaver(),
    )
    result = asyncio.run(
        agent.ainvoke(
            {"messages": [HumanMessage("Расскажи, что случилось")]},
            config=get_well_agent_config(thread_id="t1", well_id=812),
            context=WellChatContext(
                well_bound=True,
                dossier="ДОСЬЕ",
                notification="БЛОК-УВЕДОМЛЕНИЯ",
            ),
        ),
    )
    (seen,) = _RecordingModel.seen
    assert isinstance(seen[0], SystemMessage)
    assert "БЛОК-УВЕДОМЛЕНИЯ" in seen[0].content
    assert all("БЛОК-УВЕДОМЛЕНИЯ" not in str(m.content) for m in result["messages"])
