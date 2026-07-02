"""LangGraph processor for a single SPO measurement."""

from dataclasses import dataclass
from typing import Any

from langchain.messages import HumanMessage

from apps.wells.models.spo import SPO

from .base import BaseAIProcessor


@dataclass(slots=True)
class SPOProcessingInput:
    spo: SPO
    raw_s3_key: str
    chart_s3_key: str | None
    notes_s3_key: str | None
    repair_id: int


class SPOAIProcessor(BaseAIProcessor[SPOProcessingInput]):
    prompt_version: str = "v0"

    def _build_state(self, item: SPOProcessingInput) -> dict[str, Any]:
        # Prompt author: describe brigade context, expected findings,
        # output schema. Attach chart/notes via tool calls when needed.
        placeholder_prompt = (
            f"Analyze SPO measurement (spo_id={item.spo.id}, "
            f"raw_s3_key={item.raw_s3_key}, chart_s3_key={item.chart_s3_key}, "
            f"notes_s3_key={item.notes_s3_key}, "
            f"snapshot_time={item.spo.snapshot_time.isoformat()}). "
            "Return findings as JSON."
        )
        return {"messages": [HumanMessage(content=placeholder_prompt)]}
