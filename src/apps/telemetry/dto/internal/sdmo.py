from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class SdmoStationDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
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


class SdmoFcDataDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    sdmo_id: int
    sdmo_station_id: int
    day: date
    savetime: datetime
    data: dict | None
