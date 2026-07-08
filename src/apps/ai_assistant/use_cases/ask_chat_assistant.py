from collections.abc import Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, cast

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    ResponseT,
    _InputAgentState,
    _OutputAgentState,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.typing import ContextT

from apps.ai_assistant.dto.commands.ask_chat_assistant import AskChatAssistantCommand
from core import get_logger
from shared.ai.llm.messages import AIMessageDTO

if TYPE_CHECKING:
    from langchain.messages import AIMessage

logger = get_logger(__name__)


class ConfigFactory(Protocol):
    def __call__(
        self,
        thread_id: str,
        repair_id: int | None,
    ) -> RunnableConfig: ...


@dataclass
class AskChatAssistantUseCase:
    agent: CompiledStateGraph[
        AgentState[ResponseT],
        ContextT,
        _InputAgentState,
        _OutputAgentState[ResponseT],
    ]
    config_factory: ConfigFactory

    async def execute(self, command: AskChatAssistantCommand) -> AIMessageDTO:
        input_data = {"messages": [command.message]}
        result = await self.agent.ainvoke(
            input=input_data,
            config=self.config_factory(
                thread_id=command.thread_id,
                repair_id=command.repair_id,
            ),
        )
        messages = result["messages"]
        _log_token_usage(command, messages)
        last_response = cast("AIMessage", messages[-1])
        msg = last_response.model_dump()
        return AIMessageDTO.model_validate(msg)


def _log_token_usage(
    command: AskChatAssistantCommand,
    messages: Iterable[Any],
) -> None:
    messages = list(messages)
    start_idx = 0
    sent_id = getattr(command.message, "id", None)
    if sent_id is not None:
        for i, m in enumerate(messages):
            if getattr(m, "id", None) == sent_id:
                start_idx = i + 1
                break

    input_tokens = 0
    output_tokens = 0
    total_tokens = 0
    llm_calls = 0
    per_call: list[dict[str, int]] = []
    for m in messages[start_idx:]:
        usage = getattr(m, "usage_metadata", None)
        if not usage:
            continue
        llm_calls += 1
        i_tok = int(usage.get("input_tokens") or 0)
        o_tok = int(usage.get("output_tokens") or 0)
        t_tok = int(usage.get("total_tokens") or 0)
        input_tokens += i_tok
        output_tokens += o_tok
        total_tokens += t_tok
        per_call.append({"input": i_tok, "output": o_tok, "total": t_tok})

    logger.debug(
        "chat_assistant ainvoke tokens: chat=%s thread=%s repair=%s "
        "llm_calls=%d input=%d output=%d total=%d per_call=%s",
        command.chat_id,
        command.thread_id,
        command.repair_id,
        llm_calls,
        input_tokens,
        output_tokens,
        total_tokens,
        per_call,
    )
