from datetime import datetime

from pydantic import BaseModel, ConfigDict


class RepairDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    well_id: int | None
    abai_well_id: int
    repair_type_id: int
    work_list: str | None
    work_plan: str | None
    start_time: datetime
    end_time: datetime | None
