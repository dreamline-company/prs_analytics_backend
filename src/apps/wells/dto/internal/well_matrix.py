from pydantic import BaseModel

from apps.org.dto.internal.brigade import BrigadeDangerDTO, BrigadeShortDTO
from apps.repairs.dto.internal.repair import RepairDTO

WELL_STATUS_SPO = "SPO"


class WellLegendDTO(BaseModel):
    repair: RepairDTO
    brigade: BrigadeShortDTO | None = None
    dangers: list[BrigadeDangerDTO] = []


class WellMatrixItemDTO(BaseModel):
    id: int
    name: str
    is_on_repair: bool
    repair_id: int | None = None
    is_frequent_repair: bool = False
    status: str = WELL_STATUS_SPO
    legend: WellLegendDTO | None = None
