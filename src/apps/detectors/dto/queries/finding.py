from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

FindingSection = Literal["measure_request", "data_quality"]


class ListFindingsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    detector_code: str = "R10"
    # Нет даты — последняя дата, за которую есть срез.
    fix_date: date | None = None
    section: FindingSection | None = None
    kind: str | None = None
    well_id: int | None = Field(default=None, ge=1)
