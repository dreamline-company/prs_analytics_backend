from datetime import datetime

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

    Значения сырые, как в telemetry_sdmo_fc_data (koef из fc_reg не применён).
    """

    model_config = ConfigDict(from_attributes=True)

    savetime: datetime
    rotor_speed: float | None
    pump_moment: float | None
    engine_current: float | None
