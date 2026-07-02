"""Read-model DTOs for the analytics dashboard endpoint.

Each nested block mirrors what the frontend needs to render one section
(dynamograms with AI verdicts, SPOs with AI verdicts, error screens, overall
AI). AI blocks are nullable — analytics rows exist before the LLM has run.
"""

import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AIResultDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    status: str
    model_name: str | None
    prompt_version: str | None
    result: dict[str, Any] | None
    error: str | None
    processed_at: datetime.datetime | None


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
    description: str | None
    is_processed: bool
    timestamp: datetime.datetime


class DynamogramsPairDTO(BaseModel):
    before: DynamogramWithAIResultDTO | None = None
    after: DynamogramWithAIResultDTO | None = None


class RepairAnalyticsViewDTO(BaseModel):
    analytics_id: int
    repair_id: int
    is_finalized: bool
    repair_docs_id: int | None
    summary_id: int | None
    dynamograms: DynamogramsPairDTO
    spos: list[SPOWithAIResultDTO]
    error_screens: list[BrigadeErrorScreenDTO]
    overall_ai_analysis: AIResultDTO | None = None
