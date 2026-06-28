from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class ListTechRegimeByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    start_date_from: date | None = None
    start_date_to: date | None = None
