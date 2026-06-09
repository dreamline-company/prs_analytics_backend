from dataclasses import dataclass
from typing import Protocol, cast

from langchain.agents import AgentState
from langchain.agents.middleware.types import (
    ResponseT,
    _InputAgentState,
    _OutputAgentState,
)
from langchain.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langgraph.graph.state import CompiledStateGraph
from langgraph.typing import ContextT

from apps.ai_assistant.dto.commands.ask_chat_assistant import AskChatAssistantCommand
from shared.ai.llm.messages import AIMessageDTO


class ConfigFactory(Protocol):
    def __call__(self, thread_id: str) -> RunnableConfig: ...


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
            config=self.config_factory(command.chat_id),
        )
        last_response = cast("AIMessage", result["messages"][-1])
        msg = last_response.model_dump()
        return AIMessageDTO.model_validate(msg)
