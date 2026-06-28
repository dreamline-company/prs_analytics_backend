from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ListTelemetryByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    date_time_from: datetime | None = None
    date_time_to: datetime | None = None
