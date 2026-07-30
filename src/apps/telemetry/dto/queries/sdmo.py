from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ListSdmoParametersByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    start_time: datetime | None = None
    end_time: datetime | None = None
