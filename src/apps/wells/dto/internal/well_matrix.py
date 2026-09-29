from pydantic import BaseModel

from apps.org.dto.internal.brigade import BrigadeDangerDTO, BrigadeShortDTO
from apps.repairs.dto.internal.repair import RepairDTO

# Текущий статус скважины в матрице при незавершённом ремонте ABAI: код
# последней работы из событий КБРС за этот ремонт, а пока кодов нет — «ПРС».
# Без ремонта статуса нет.
WELL_STATUS_WORK = "Работа [{code}]"
WELL_STATUS_PRS = "ПРС"


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
    status: str | None = None
    legend: WellLegendDTO | None = None
