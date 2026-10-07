from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

IncidentStatus = Literal["active", "normalized"]
IncidentLevel = Literal["warning", "alarm"]
# Отметка проверки; pending включает и ещё не проверенные эпизоды.
IncidentVerdict = Literal[
    "pending",
    "false_alarm",
    "failure_likely",
    "failure_confirmed",
    "undetermined",
]


class ListIncidentsByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
    detector_code: str | None = None
    reason_code: str | None = None
    status: IncidentStatus | None = None
    level: IncidentLevel | None = None
    verdict: IncidentVerdict | None = None
    # Период по физическому началу эпизода (opened_at), а не по detected_at:
    # выгрузку смотрят относительно событий на скважине, а не прогонов правила.
    opened_from: datetime | None = None
    opened_to: datetime | None = None
    limit: int = Field(default=200, ge=1, le=1000)
    offset: int = Field(default=0, ge=0)
