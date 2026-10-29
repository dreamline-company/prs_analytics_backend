from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from apps.detectors.dto.internal.well_status import WellIncidentStatusDTO
from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.telemetry.dto.internal.well_rates import WellRatesDTO
from apps.wells.dto.internal.coord_point import WellCoordPointDTO
from apps.wells.dto.internal.well_state import WellStateDTO


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

    # SdmoFcData (последний отсчёт по привязанным станциям СДМО), значения
    # приведены по koef справочника fc_reg для типа станции (см. sdmo_scaled).
    pump_moment: float | None  # Момент насоса — регистр 1991
    pump_speed: float | None  # Скорость насоса — регистр 1998
    # Единицы скорости по типу станции: «ход/мин» у ШГН, «об/мин» у ЭВН;
    # None — тип станции неизвестен.
    pump_speed_units: str | None = None
    pump_fill: float | None  # Заполнение насоса, % — регистр 1997
    sdmo_time: datetime | None  # Время отсчёта — SdmoFcData.savetime
    # False — у станции нет type_1900 (или регистра нет в справочнике для её
    # типа): pump_* отданы сырыми, как в telemetry_sdmo_fc_data.
    sdmo_scaled: bool = False
    # Статус станции (1 — онлайн, 0 — не онлайн) — регистр 1999 «Статус
    # (VLT SALT)», последнее заполненное значение по станциям скважины.
    sdmo_vlt_status: int | None
    # Время отсчёта, из которого взят sdmo_vlt_status (SdmoFcData.savetime).
    # Регистр приходит не в каждом отсчёте, поэтому может быть раньше sdmo_time.
    sdmo_vlt_status_time: datetime | None = None
    # ГДИС: динамический уровень, м, по последнему исследованию с этой
    # метрикой («H дин, м» / «Динамический уровень, м») и дата исследования.
    h_din_m: float | None
    h_din_date: date | None
    zero_rate_days: int  # Дней с дебитом 0 — пока заглушка
    # Факт нефти с начала месяца (под месячный план техрежима), с учётом
    # простоев: медиана замеров ЦИТС в работе × доля времени работы
    # (WellMonthProductionService). None — нет замеров в работе или доли
    # работы не узнать.
    oil_rate_month: float | None = None
    # Та же медиана без учёта простоев — дебит, когда скважина работает.
    oil_rate_month_working: float | None = None
    # Доля времени работы с 1-го числа (0…1) и откуда она: sdmo / abai.
    work_share_month: float | None = None
    work_share_month_source: str | None = None
    # Сколько замеров в медиане и с какого (меньше трёх в месяце — берутся
    # три последних, первый может быть из прошлого месяца).
    month_measurements: int = 0
    month_period_from: datetime | None = None


class WellCardDTO(BaseModel):
    well_id: int
    well_name: str
    device: str | None
    # Ручной статус из wells_well_status_history: работает / не работает.
    status: WellCardStatusDTO | None
    # Итоговое состояние скважины (ремонт, ABAI, привод СДМО, детекторы) —
    # то же, что в строке матрицы; None — скважина ещё не посчитана.
    state: WellStateDTO | None = None
    # Что видят детекторы прямо сейчас; при отсутствии активных эпизодов —
    # level="normal" и «Нарушений не выявлено».
    incident_status: WellIncidentStatusDTO
    # Идущий ремонт (начат, не закрыт) или None, если скважина не в ремонте.
    current_repair: CurrentRepairDTO | None
    coord: WellCoordPointDTO | None
    passport: WellCardPassportDTO
