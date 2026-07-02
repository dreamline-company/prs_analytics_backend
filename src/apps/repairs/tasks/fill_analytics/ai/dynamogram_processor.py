"""LangGraph processor for a single dynamogram (before or after repair)."""

from dataclasses import dataclass
from typing import Any

from langchain.messages import HumanMessage

from apps.wells.models.dynamogram import Dynamogram

from .base import BaseAIProcessor


@dataclass(slots=True)
class DynamogramProcessingInput:
    """Everything the prompt author might want when analyzing a dynamogram."""

    dynamogram: Dynamogram
    role: str  # "before" | "after"
    s3_key: str  # underlying File.file
    repair_id: int


class DynamogramAIProcessor(BaseAIProcessor[DynamogramProcessingInput]):
    prompt_version: str = "v0"

    def _build_state(self, item: DynamogramProcessingInput) -> dict[str, Any]:
        # Prompt author: replace with the real prompt (image reference,
        # before/after context, expected JSON schema, etc.). The state shape
        # matches what ``create_agent`` in this project already expects.
        placeholder_prompt = (
            f"Analyze the {item.role}-repair dynamogram "
            f"(dynamogram_id={item.dynamogram.id}, s3_key={item.s3_key}, "
            f"snapshot_time={item.dynamogram.snapshot_time.isoformat()}). "
            "Return findings as JSON."
        )
        return {"messages": [HumanMessage(content=placeholder_prompt)]}
