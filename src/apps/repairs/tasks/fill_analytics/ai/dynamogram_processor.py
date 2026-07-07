"""LangGraph processor for a single dynamogram (before or after repair)."""

import base64
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
    image_bytes: bytes | None = None
    image_mime: str = "image/png"


class DynamogramAIProcessor(BaseAIProcessor[DynamogramProcessingInput]):
    prompt_version: str = "v1"

    def _build_state(self, item: DynamogramProcessingInput) -> dict[str, Any]:
        placeholder_prompt = (
            f"Analyze the {item.role}-repair dynamogram "
            f"(dynamogram_id={item.dynamogram.id}, "
            f"snapshot_time={item.dynamogram.snapshot_time.isoformat()}). "
            "The image is attached below. Return findings as JSON."
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": placeholder_prompt}]
        if item.image_bytes is not None:
            b64 = base64.b64encode(item.image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.image_mime};base64,{b64}"},
                },
            )
        return {"messages": [HumanMessage(content=content)]}
