from datetime import datetime

from pydantic import BaseModel, ConfigDict

from shared.dto.api import AppResponse


class RodBreakDetectionReadDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    run_id: int
    well_id: int
    station_sdmo_id: int | None
    detector_code: str
    fired: bool
    fired_at: datetime | None
    failure_dt: datetime | None
    lead_time_hours: float | None
    event_class: str | None
    base_moment: float | None
    low_confidence: bool
    created_at: datetime


class ListRodBreakDetectionsResponse(AppResponse[list[RodBreakDetectionReadDTO]]): ...
