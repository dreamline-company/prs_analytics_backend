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


# Читать fc_data DTO-моделью (108 колонок) сейчас не нужно — детектор берёт
# отдельные регистры SQL-выборкой. При появлении API можно добавить.
