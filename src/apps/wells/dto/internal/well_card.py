from datetime import datetime

from pydantic import BaseModel, ConfigDict

from apps.wells.dto.internal.coord_point import WellCoordPointDTO


class WellCardStatusDTO(BaseModel):
    """Последняя запись из wells_well_status_history."""

    model_config = ConfigDict(from_attributes=True)

    is_working: bool
    reason: str | None
    created_at: datetime


class WellCardPassportDTO(BaseModel):
    """Паспорт скважины — по последним доступным отсчётам каждого источника."""

    # Telemetry (последняя запись по скважине).
    oil_rate: float | None  # Дебит нефти, т/сут — Telemetry.qm_oil
    liquid_rate: float | None  # Дебит жидкости, т/сут — Telemetry.qv_liquid
    water_cut: float | None  # Обводнённость, % — считается из дебитов
    # TechRegime (последний режим по скважине).
    plan_oil_rate: float | None  # План Qн, т/сут — TechRegime.oil
    # SdmoFcData (последний отсчёт по привязанным станциям СДМО).
    pump_moment: float | None  # Момент насоса — регистр 1991
    pump_speed: float | None  # Скорость насоса, об/мин — регистр 1998
    pump_fill: float | None  # Заполнение насоса, % — регистр 1997
    zero_rate_days: int  # Дней с дебитом 0 — пока заглушка


class WellCardDTO(BaseModel):
    well_id: int
    well_name: str
    device: str | None
    status: WellCardStatusDTO | None
    coord: WellCoordPointDTO | None
    passport: WellCardPassportDTO
