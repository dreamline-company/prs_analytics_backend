from pydantic import BaseModel

from apps.detectors.dto.internal.well_status import WellIncidentStatusDTO
from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.telemetry.dto.internal.well_rates import WellRatesDTO


class WellMatrixIncidentWellDTO(BaseModel):
    id: int
    well_name: str


class WellMatrixIncidentExplDTO(BaseModel):
    """Способ эксплуатации по последнему периоду скважины."""

    name_ru: str | None


class WellMatrixIncidentDTO(BaseModel):
    well: WellMatrixIncidentWellDTO
    expl: WellMatrixIncidentExplDTO | None = None
    # Те же блоки, что в карточке скважины: строка матрицы и карточка должны
    # показывать одно и то же состояние, а не расходиться в трактовках.
    incident_status: WellIncidentStatusDTO
    current_repair: CurrentRepairDTO | None = None
    # Дебиты факт/план — та же общая часть, что в паспорте карточки. Параметры
    # насоса сюда не входят: они требуют выборки по telemetry_sdmo_fc_data на
    # каждую станцию НГДУ, а в матрице нужны показатели скважины, не привода.
    passport: WellRatesDTO
