# ruff: noqa: RUF001
"""LangGraph processor for a single dynamogram (before or after repair).

Returns a strict-JSON verdict that includes the pump efficiency (КПД), so the
KPI module can read a real AI-derived value for "КПД насоса после ПРС" and for
the dynamogram before/after comparison.
"""

import base64
import json
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


_OUTPUT_SCHEMA_HINT = {
    "pump_efficiency_pct": "число 0..100 — оценка КПД насоса по динамограмме",
    "fill_pct": "число 0..100 — коэффициент заполнения насоса",
    "condition": "строка — краткое состояние (норма/недозаполнение/утечка/...)",
    "score": "целое 0..100 — общая оценка состояния по динамограмме",
    "issues": ["строки — обнаруженные дефекты"],
    "verdict": "строка — короткий текстовый вывод по-русски",
}


class DynamogramAIProcessor(BaseAIProcessor[DynamogramProcessingInput]):
    prompt_version: str = "v2"

    def _build_state(self, item: DynamogramProcessingInput) -> dict[str, Any]:
        prompt = (
            "Ты — инженер по механизированной добыче. Проанализируй динамограмму "
            f"{'ДО' if item.role == 'before' else 'ПОСЛЕ'} ремонта "
            f"(dynamogram_id={item.dynamogram.id}, "
            f"snapshot_time={item.dynamogram.snapshot_time.isoformat()}). "
            "Изображение приложено ниже.\n\n"
            "Верни СТРОГО валидный JSON — без пояснений, без markdown-обёртки. "
            "Все поля обязательны; если значение оценить нельзя — ставь null "
            "(для списков — пустой список).\n\n"
            "Схема ответа:\n"
            f"{json.dumps(_OUTPUT_SCHEMA_HINT, ensure_ascii=False)}\n"
        )
        content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
        if item.image_bytes is not None:
            b64 = base64.b64encode(item.image_bytes).decode("ascii")
            content.append(
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:{item.image_mime};base64,{b64}"},
                },
            )
        return {"messages": [HumanMessage(content=content)]}
