from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, cast

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    ResponseT,
    _InputAgentState,
    _OutputAgentState,
)
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.typing import ContextT

from apps.ai_assistant.dto.commands.ask_well_chat_assistant import (
    AskWellChatAssistantCommand,
)
from core import get_logger
from shared.ai.llm.messages import AIMessageDTO

if TYPE_CHECKING:
    from langchain.messages import AIMessage

logger = get_logger(__name__)


class WellConfigFactory(Protocol):
    def __call__(
        self,
        thread_id: str,
        well_id: int | None,
    ) -> RunnableConfig: ...


@dataclass
class AskWellChatAssistantUseCase:
    agent: CompiledStateGraph[
        AgentState[ResponseT],
        ContextT,
        _InputAgentState,
        _OutputAgentState[ResponseT],
    ]
    config_factory: WellConfigFactory

    async def execute(self, command: AskWellChatAssistantCommand) -> AIMessageDTO:
        result = await self.agent.ainvoke(
            input={"messages": [command.message]},
            config=self.config_factory(
                thread_id=command.thread_id,
                well_id=command.well_id,
            ),
        )
        messages = result["messages"]
        last_response = cast("AIMessage", messages[-1])
        usage = getattr(last_response, "usage_metadata", None) or {}
        logger.debug(
            "well_chat_assistant ainvoke: chat=%s thread=%s well=%s tokens=%s",
            command.chat_id,
            command.thread_id,
            command.well_id,
            usage.get("total_tokens"),
        )
        return AIMessageDTO.model_validate(last_response.model_dump())
