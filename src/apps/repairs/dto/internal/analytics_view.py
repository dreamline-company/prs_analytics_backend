"""Read-model DTOs for the analytics dashboard endpoint.

Each nested block mirrors what the frontend needs to render one section
(dynamograms with AI verdicts, SPOs with AI verdicts, error screens, overall
AI). AI blocks are nullable — analytics rows exist before the LLM has run.
"""

import datetime
import json
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator

from apps.org.dto.internal.brigade import BrigadeShortDTO
from apps.wells.dto.internal.well import WellShortDTO


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


class SPOPassportDTO(BaseModel):
    """Mirrors ``shared.integrations.kbrs.api.dtos.MeasurementPassportDto``.

    Populated from the SPO ``passport.json`` sidecar in S3.
    """

    device_id: int | None = None
    device_version: str | None = None
    organization: str | None = None
    workshop: int | None = None
    brigade: int | None = None
    spu: int | None = None
    field_id: int | None = None
    bush: int | None = None
    well: int | None = None
    max_hook_weight_t: float | None = None
    tackle_block_ratio: int | None = None
    tare_weight_t: float | None = None
    values: dict[str, Any] = {}


class SPOWithAIResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    file_id: int
    chart_file_id: int | None
    chart_json_file_id: int | None
    notes_file_id: int | None
    passport_file_id: int | None
    snapshot_time: datetime.datetime
    start_time: datetime.datetime | None = None
    end_time: datetime.datetime | None = None
    well_id: int
    passport: SPOPassportDTO | None = None
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


class RepairMetaDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    start_time: datetime.datetime
    end_time: datetime.datetime | None = None


class RepairAnalyticsOverallInfoDTO(BaseModel):
    """Top-level context for the repair being viewed: which well, which brigade
    (nullable — a repair may have no assigned brigade), and repair timing.
    """

    well: WellShortDTO
    brigade: BrigadeShortDTO | None = None
    repair: RepairMetaDTO


class RepairAnalyticsViewDTO(BaseModel):
    analytics_id: int
    repair_id: int
    is_finalized: bool
    repair_docs_id: int | None
    summary_id: int | None
    overall_info: RepairAnalyticsOverallInfoDTO
    dynamograms: DynamogramsPairDTO
    spos: list[SPOWithAIResultDTO]
    error_screens: list[BrigadeErrorScreenDTO]
    overall_ai_analysis: OverallAIResultDTO | None = None
    transports: list[RepairTransportViewDTO] = []
