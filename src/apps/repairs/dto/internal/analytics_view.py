"""Read-model DTOs for the analytics dashboard endpoint.

Each nested block mirrors what the frontend needs to render one section
(dynamograms with AI verdicts, SPOs with AI verdicts, error screens, overall
AI). AI blocks are nullable — analytics rows exist before the LLM has run.
"""

import datetime
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class AIResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    model_name: str | None
    prompt_version: str | None
    result: dict[str, Any] | None
    error: str | None
    processed_at: datetime.datetime | None

    @field_validator("result", mode="before")
    @classmethod
    def _parse_result(cls, value: Any) -> Any:  # noqa: ANN401
        if isinstance(value, str):
            try:
                return json.loads(value)
            except json.JSONDecodeError:
                return {"raw": value}
        return value


class OverallAIVerdictDTO(BaseModel):
    """Parsed structured output of the overall AI analysis (prompt v2+)."""

    score: int
    good_points: list[str] = []
    risks_and_issues: list[str] = []
    attention_points: list[str] = []
    recommendations: list[str] = []


class OverallAIResultDTO(BaseModel):
    id: int
    status: str
    model_name: str | None
    prompt_version: str | None
    error: str | None
    processed_at: datetime.datetime | None
    verdict: OverallAIVerdictDTO | None
    raw: str | None


class DynamogramWithAIResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    snapshot_time: datetime.datetime
    well_id: int
    ai_result: AIResultDTO | None = None


class SPOWithAIResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    chart_file_id: int | None
    notes_file_id: int | None
    snapshot_time: datetime.datetime
    well_id: int
    ai_result: AIResultDTO | None = None


class BrigadeErrorScreenDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    brigade_id: int
    screen: str | None
    screen_url: str | None = None
    description: str | None
    is_processed: bool
    timestamp: datetime.datetime


class DynamogramsPairDTO(BaseModel):
    before: DynamogramWithAIResultDTO | None = None
    after: DynamogramWithAIResultDTO | None = None


class RepairTransportViewDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repair_id: int

    request_id: int | None
    operation_code: str | None
    operation_number: str | None

    status_id: int | None
    status_name: str | None
    closure_status: str | None

    department: str | None
    position: str | None

    operation_created_at: datetime.datetime | None
    planned_start_at: datetime.datetime | None
    planned_end_at: datetime.datetime | None
    actual_date: datetime.datetime | None

    engine_hours: float | None
    mileage: float | None

    transport_equipment_number: int | None

    company: str | None
    division: str | None
    bpl: str | None

    well_number: str | None
    work_type: str | None

    vehicle_number: str | None
    vehicle_class_code: str | None
    vehicle_class_name: str | None


class RepairAnalyticsViewDTO(BaseModel):
    analytics_id: int
    repair_id: int
    is_finalized: bool
    repair_docs_id: int | None
    summary_id: int | None
    dynamograms: DynamogramsPairDTO
    spos: list[SPOWithAIResultDTO]
    error_screens: list[BrigadeErrorScreenDTO]
    overall_ai_analysis: OverallAIResultDTO | None = None
    transports: list[RepairTransportViewDTO] = []
