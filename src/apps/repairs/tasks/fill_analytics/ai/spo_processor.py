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
    chart_text: str | None = None
    notes_text: str | None = None


class SPOAIProcessor(BaseAIProcessor[SPOProcessingInput]):
    prompt_version: str = "v1"

    def _build_state(self, item: SPOProcessingInput) -> dict[str, Any]:
        parts: list[str] = [
            f"Analyze SPO measurement (spo_id={item.spo.id}, "
            f"snapshot_time={item.spo.snapshot_time.isoformat()}). "
            "Chart CSV and notes JSON are attached below. Return findings as JSON.",
        ]
        if item.chart_text is not None:
            parts.append(f"chart.csv:\n```csv\n{item.chart_text}\n```")
        if item.notes_text is not None:
            parts.append(f"notes.json:\n```json\n{item.notes_text}\n```")
        return {"messages": [HumanMessage(content="\n\n".join(parts))]}
