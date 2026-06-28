from datetime import date

from pydantic import BaseModel, ConfigDict


class TechRegimeDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    abai_well_id: int
    start_date: date
    end_date: date
    liquid: float | None
    oil: float | None
