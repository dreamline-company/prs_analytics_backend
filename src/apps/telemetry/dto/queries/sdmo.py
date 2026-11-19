from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ListSdmoParametersByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    start_time: datetime | None = None
    end_time: datetime | None = None


class ListSdmoDriveSegmentsByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
    # Пропуск пригодных отсчётов дольше — отрезок no_data.
    no_data_gap_minutes: int = Field(default=30, ge=1, le=24 * 60)
