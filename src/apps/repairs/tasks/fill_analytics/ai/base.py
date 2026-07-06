"""Common AI-processor plumbing.

A processor owns a LangGraph agent, invokes it with a prompt/state, and
returns a normalized ``AIProcessingResult``. Concrete processors override
``prompt_version``, ``_build_state()``, and (optionally) ``_parse_output()``.

Prompts and graph topology are intentionally left as placeholders — the
project owner writes them later; the plumbing is stable.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from langchain.messages import AIMessage

from core import get_logger
from core.settings import get_settings

settings = get_settings()

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

logger = get_logger(__name__)


@dataclass(slots=True)
class AIProcessingResult:
    """Outcome of one LLM run, in a shape the persistence layer stores."""

    status: str  # "completed" | "failed"
    result: dict[str, Any] | None
    error: str | None
    model_name: str | None
    prompt_version: str | None
    processed_at: datetime


class BaseAIProcessor[InputT]:
    """Base class for single-item AI processors.

    Subclasses provide:
      * ``prompt_version`` — bump when the prompt/graph changes materially.
      * ``_build_state(input)`` — the initial state passed to the agent.
      * ``_parse_output(state)`` — extracts a JSON-serializable dict from the
        agent's final state. Defaults to picking the last AIMessage content.
    """

    prompt_version: str = "v0"

    def __init__(
        self,
        agent: CompiledStateGraph,
        model_name: str | None = None,
    ) -> None:
        self._agent = agent
        self._model_name = model_name

    async def process(self, item: InputT) -> AIProcessingResult:
        processed_at = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        try:
            initial_state = self._build_state(item)
            final_state = await self._agent.ainvoke(input=initial_state)
            payload = self._parse_output(final_state)
        except Exception as exc:
            logger.exception("AI processor %s failed.", type(self).__name__)
            return AIProcessingResult(
                status="failed",
                result=None,
                error=str(exc),
                model_name=self._model_name,
                prompt_version=self.prompt_version,
                processed_at=processed_at,
            )

        return AIProcessingResult(
            status="completed",
            result=payload,
            error=None,
            model_name=self._model_name,
            prompt_version=self.prompt_version,
            processed_at=processed_at,
        )

    def _build_state(self, item: InputT) -> dict[str, Any]:
        raise NotImplementedError

    def _parse_output(self, final_state: Any) -> dict[str, Any]:  # noqa: ANN401
        messages = (
            final_state.get("messages", []) if isinstance(final_state, dict) else []
        )
        last = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        if last is None:
            return {"raw": None}
        content = last.content
        if isinstance(content, str):
            try:
                return {"raw": content, "parsed": json.loads(content)}
            except json.JSONDecodeError:
                return {"raw": content}
        return {"raw": content}
