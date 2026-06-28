import datetime

from shared.dto.repositories import RepositoryDTO


class CreateRepairSummaryDTO(RepositoryDTO):
    repair_id: int | None = None
    well_id: int
    second_well_id: int | None = None
    date: datetime.date
    brigade_number: int
    pump_type: str
    shift_type_number: int
    car: str
    device_number: str
    shift_details: list[str]


class UpdateRepairSummaryDTO(RepositoryDTO):
    repair_id: int | None = None
    well_id: int | None = None
    second_well_id: int | None = None
    date: datetime.date | None = None
    brigade_number: int | None = None
    pump_type: str | None = None
    shift_type_number: int | None = None
    car: str | None = None
    device_number: str | None = None
    shift_details: list[str] | None = None
