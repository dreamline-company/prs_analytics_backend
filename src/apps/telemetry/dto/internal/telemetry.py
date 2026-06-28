from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TelemetryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    well_id: int
    date_time: datetime
    qv_liquid: float | None
    qm_oil: float | None
    ngdu_id: int
    oil_field: str | None
    gzu: str | None
    otvod: int | None
