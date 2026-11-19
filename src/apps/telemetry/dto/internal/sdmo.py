from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class SdmoStationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_ngdu_id: int
    sdmo_id: int
    place_id: int | None
    name: str | None
    code: str | None
    type_1900: int | None
    serial_number: str | None
    active: bool | None
    status: int | None
    well_id: int | None


class SdmoFcRegDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sdmo_id: int
    type_1900: int | None
    addr: int
    name: str
    units: str | None
    koef: float | None
    type: str | None
    dynamic: bool | None
    info: str
    lora_bytes_size: int | None


# Читать fc_data полной DTO-моделью (108 колонок) сейчас не нужно — детектор и
# API берут отдельные регистры SQL-выборкой.


class SdmoParametersDTO(BaseModel):
    """Срез параметров СДМО на один отсчёт savetime.

    Значения приведены по koef справочника fc_reg для типа станции отсчёта.
    ``scaled=False`` — у станции нет type_1900 (или регистра нет в справочнике
    для её типа): значения сырые, как в telemetry_sdmo_fc_data.
    """

    model_config = ConfigDict(from_attributes=True)

    savetime: datetime
    rotor_speed: float | None
    pump_moment: float | None
    engine_current: float | None
    scaled: bool = False


class SdmoDriveSegmentDTO(BaseModel):
    """Отрезок подряд идущих отсчётов станции с одним кодом статуса 1999.

    Времена — как ``savetime`` СДМО (местные). Первый отрезок может начинаться
    раньше ``start_time``: это последний пригодный отсчёт до окна, состояние на
    левом краю.
    """

    start_at: datetime
    end_at: datetime
    # run — работает (в т. ч. с предупреждением), stop — стоит, alarm —
    # остановка по аварии, no_data — нет пригодных отсчётов дольше порога.
    state: Literal["run", "stop", "alarm", "no_data"]
    # Сырой код 1999; None у no_data.
    code: int | None
    # Подпись кода по РЭ для типа станции; None у no_data.
    reason_ru: str | None
    # Пригодных отсчётов в отрезке (у no_data — 0).
    packets: int


class SdmoDriveStationDTO(BaseModel):
    """Отрезки одной станции скважины: у скважины их бывает две (замена
    станции), каждая считается отдельно."""

    station_id: int
    station_name: str | None
    type_1900: int | None
    segments: list[SdmoDriveSegmentDTO]
