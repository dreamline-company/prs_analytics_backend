"""LangGraph processor for the whole-repair analysis.

Consumes the per-item results (dynamogram-before, dynamogram-after, SPOs) and
produces one aggregated verdict for the repair. This is meant to be triggered
only once the component results are available.
"""

from dataclasses import dataclass, field
from typing import Any

from langchain.messages import HumanMessage

from apps.repairs.models.repair import Repair

from .base import BaseAIProcessor


@dataclass(slots=True)
class OverallProcessingInput:
    repair: Repair
    dynamogram_before_result: dict | None = None
    dynamogram_after_result: dict | None = None
    spo_results: list[dict] = field(default_factory=list)


class OverallAIProcessor(BaseAIProcessor[OverallProcessingInput]):
    prompt_version: str = "v1"

    def _build_state(self, item: OverallProcessingInput) -> dict[str, Any]:
        # Prompt author: design the aggregation prompt (compare before vs
        # after, summarize SPO anomalies, produce a repair-level verdict).
        summary = {
            "repair_id": item.repair.id,
            "repair_abai_id": item.repair.abai_id,
            "start_time": item.repair.start_time.isoformat(),
            "end_time": (
                item.repair.end_time.isoformat()
                if item.repair.end_time is not None
                else None
            ),
            "dynamogram_before_result": item.dynamogram_before_result,
            "dynamogram_after_result": item.dynamogram_after_result,
            "spo_results": item.spo_results,
        }
        placeholder_prompt = (
            "Aggregate per-item analyses into a repair-level verdict. "
            f"Input JSON: {summary}. Return findings as JSON."
        )
        return {"messages": [HumanMessage(content=placeholder_prompt)]}
