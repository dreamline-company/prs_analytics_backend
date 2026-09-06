from datetime import datetime

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


class WellMatrixIncidentPassportDTO(WellRatesDTO):
    """Паспорт строки матрицы: дебиты факт/план, даты замеров, статус станции.

    Даты те же, что в паспорте карточки скважины: ``telemetry_time`` (замер
    дебитов), ``tech_regime_date`` (начало режима) и ``sdmo_time`` (последний
    отсчёт СДМО по станциям скважины). ``sdmo_vlt_status`` — тот же статус
    станции, что в карточке. Параметры насоса в матрицу не входят.
    """

    sdmo_time: datetime | None = None  # Время отсчёта — SdmoFcData.savetime
    # Статус станции (1 — онлайн, 0 — не онлайн) — регистр 1999 «Статус
    # (VLT SALT)», последнее заполненное значение по станциям скважины.
    sdmo_vlt_status: int | None = None


class WellMatrixIncidentDTO(BaseModel):
    well: WellMatrixIncidentWellDTO
    expl: WellMatrixIncidentExplDTO | None = None
    # Те же блоки, что в карточке скважины: строка матрицы и карточка должны
    # показывать одно и то же состояние, а не расходиться в трактовках.
    incident_status: WellIncidentStatusDTO
    current_repair: CurrentRepairDTO | None = None
    # Дебиты факт/план и даты последних замеров — как в паспорте карточки.
    passport: WellMatrixIncidentPassportDTO
