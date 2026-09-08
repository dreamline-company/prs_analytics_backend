from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from apps.detectors.dto.internal.well_status import WellIncidentStatusDTO
from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.telemetry.dto.internal.well_rates import WellRatesDTO
from apps.wells.dto.internal.coord_point import WellCoordPointDTO


class WellCardStatusDTO(BaseModel):
    """Последняя запись из wells_well_status_history."""

    model_config = ConfigDict(from_attributes=True)

    is_working: bool
    reason: str | None
    created_at: datetime


class WellCardPassportDTO(WellRatesDTO):
    """Паспорт скважины — по последним доступным отсчётам каждого источника.

    Дебиты (факт и план) наследуются от общей части, которую карточка делит со
    строкой матрицы; здесь к ним добавляется оборудование.
    """

    # SdmoFcData (последний отсчёт по привязанным станциям СДМО).
    pump_moment: float | None  # Момент насоса — регистр 1991
    pump_speed: float | None  # Скорость насоса, об/мин — регистр 1998
    pump_fill: float | None  # Заполнение насоса, % — регистр 1997
    sdmo_time: datetime | None  # Время отсчёта — SdmoFcData.savetime
    # Статус станции (1 — онлайн, 0 — не онлайн) — регистр 1999 «Статус
    # (VLT SALT)», последнее заполненное значение по станциям скважины.
    sdmo_vlt_status: int | None
    # ГДИС: динамический уровень, м, по последнему исследованию с этой
    # метрикой («H дин, м» / «Динамический уровень, м») и дата исследования.
    h_din_m: float | None
    h_din_date: date | None
    zero_rate_days: int  # Дней с дебитом 0 — пока заглушка


class WellCardDTO(BaseModel):
    well_id: int
    well_name: str
    device: str | None
    # Ручной статус из wells_well_status_history: работает / не работает.
    status: WellCardStatusDTO | None
    # Что видят детекторы прямо сейчас; при отсутствии активных эпизодов —
    # level="normal" и «Работает в штатном режиме».
    incident_status: WellIncidentStatusDTO
    # Идущий ремонт (начат, не закрыт) или None, если скважина не в ремонте.
    current_repair: CurrentRepairDTO | None
    coord: WellCoordPointDTO | None
    passport: WellCardPassportDTO
