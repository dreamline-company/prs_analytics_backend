from datetime import datetime

from pydantic import BaseModel, ConfigDict

from apps.wells.dto.internal.well import WellShortDTO


class BrigadeDTO(BaseModel):
    """Brigade view with placeholder analytics fields.

    ``violations_count`` and ``is_in_repair`` are exposed now for the frontend
    contract but not yet computed — kept as defaults until the counter and
    in-repair detection are implemented.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ngdu_id: int
    cdng: str | None = None
    fio: str | None = None
    lift: str | None = None
    field: str | None = None
    device: str | None = None
    violations_count: int = 0
    is_in_repair: bool = False


class FrequentRepairBrigadeDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ngdu_id: int


class BrigadesKPIDTO(BaseModel):
    total_brigades: int
    in_repair_now: int
    with_violations: int
    without_violations: int
    avg_repair_hours: float | None
    frequent_repair_brigades: list[FrequentRepairBrigadeDTO]


class RepairShortDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    well_id: int | None
    abai_well_id: int
    repair_type_id: int
    start_time: datetime
    end_time: datetime | None


class RepairStateEventDTO(BaseModel):
    time: datetime
    description: str


class CurrentRepairDTO(BaseModel):
    well: WellShortDTO
    repair: RepairShortDTO
    violations_count: int
    por_percent: int
    spo_percent: int
    vehicles_count: int
    last_event: RepairStateEventDTO | None


class BrigadeRepairStateDTO(BaseModel):
    is_in_repair: bool
    current_repair: CurrentRepairDTO | None


class BrigadeShortDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    ngdu_id: int


class BrigadeDangerDTO(BaseModel):
    type: str
    time: datetime
    description: str


class BrigadeDangerZoneItemDTO(BaseModel):
    brigade: BrigadeShortDTO
    dangers: list[BrigadeDangerDTO]
