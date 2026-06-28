import datetime

from pydantic import BaseModel, ConfigDict


class RepairSummaryDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    repair_id: int | None
    well_id: int
    second_well_id: int | None
    date: datetime.date
    brigade_number: int
    pump_type: str
    shift_type_number: int
    car: str
    device_number: str
    shift_details: list[str]
