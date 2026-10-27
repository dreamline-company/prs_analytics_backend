"""Tools контура компенсации скважинного ассистента — без базы."""

import asyncio
from types import SimpleNamespace
from typing import Any, Self

from apps.ai_assistant.ai.well_chat_assistant.tools import (
    WELL_CHAT_ASSISTANT_TOOLS,
)
from apps.ai_assistant.ai.well_chat_assistant.tools import (
    compensation as compensation_tools,
)


class _Session:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None


def _donor(recommendation_id: int, name: str, gain: float) -> dict[str, Any]:
    return {
        "recommendation_id": recommendation_id,
        "well": {"id": recommendation_id, "well_name": name},
        "gain": gain,
        "risk": "low",
        "distance_m": 900,
        "status": "pending",
    }


class _ContourUseCase:
    def __init__(self, _session: object) -> None: ...

    async def execute(self, _query: object) -> SimpleNamespace:
        donors = [_donor(i, f"VMB_{i:04d}", 0.5) for i in range(1, 13)]
        data = {"state": "stopped", "main_donors": donors}
        return SimpleNamespace(model_dump=lambda **_kw: data)


class _Pairs:
    def __init__(self, _session: object) -> None: ...

    async def list_open_by_loss_well_ids(self, _ids: list[int]) -> list:
        return [
            SimpleNamespace(id=1, speed_from=5.5, speed_to=6.3),
            SimpleNamespace(id=2, speed_from=200.0, speed_to=230.0),
        ]


def test_contour_adds_donor_speeds_and_limits_list(monkeypatch: Any) -> None:  # noqa: ANN401
    monkeypatch.setattr(compensation_tools, "session_makers", {"app": _Session})
    monkeypatch.setattr(
        compensation_tools,
        "GetCompensationContourUseCase",
        _ContourUseCase,
    )
    monkeypatch.setattr(
        compensation_tools,
        "CompensationRecommendationRepository",
        _Pairs,
    )

    result = asyncio.run(
        compensation_tools.get_compensation_contour.ainvoke(
            {},
            config={"configurable": {"well_id": 42}},
        ),
    )

    assert result["donors_total"] == 12
    assert len(result["main_donors"]) == compensation_tools.DONORS_LIMIT
    first, second, third = result["main_donors"][:3]
    assert (first["speed_from"], first["speed_to"]) == (5.5, 6.3)
    assert (second["speed_from"], second["speed_to"]) == (200.0, 230.0)
    assert (third["speed_from"], third["speed_to"]) == (None, None)


def test_field_donors_status_filter_is_enum_for_model() -> None:
    tool = next(t for t in WELL_CHAT_ASSISTANT_TOOLS if t.name == "list_field_donors")
    schema = tool.tool_call_schema.model_json_schema()

    status = schema["properties"]["status"]
    variants = status.get("anyOf", [status])
    enums = [v["enum"] for v in variants if "enum" in v]
    assert enums == [["pending", "accepted", "applied", "rejected", "reserve"]]
