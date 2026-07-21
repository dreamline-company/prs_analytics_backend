from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class CreateSdmoStationDTO(RepositoryDTO):
    sdmo_id: int
    place_id: int | None = None
    name: str | None = None
    code: str | None = None
    type_1900: int | None = None
    serial_number: str | None = None
    active: bool | None = None
    status: int | None = None
    well_id: int | None = None


class UpdateSdmoStationDTO(RepositoryDTO):
    sdmo_id: int | None = None
    place_id: int | None = None
    name: str | None = None
    code: str | None = None
    type_1900: int | None = None
    serial_number: str | None = None
    active: bool | None = None
    status: int | None = None
    well_id: int | None = None


class CreateSdmoFcRegDTO(RepositoryDTO):
    sdmo_id: int
    type_1900: int | None = None
    addr: int
    name: str
    units: str | None = None
    koef: float | None = None
    type: str | None = None
    dynamic: bool | None = None
    info: str
    lora_bytes_size: int | None = None


class UpdateSdmoFcRegDTO(RepositoryDTO):
    sdmo_id: int | None = None
    type_1900: int | None = None
    addr: int | None = None
    name: str | None = None
    units: str | None = None
    koef: float | None = None
    type: str | None = None
    dynamic: bool | None = None
    info: str | None = None
    lora_bytes_size: int | None = None


class CreateSdmoFcDataDTO(RepositoryDTO):
    sdmo_id: int
    sdmo_station_id: int
    day: date
    savetime: datetime
    data: dict | None = None


class UpdateSdmoFcDataDTO(RepositoryDTO):
    sdmo_id: int | None = None
    sdmo_station_id: int | None = None
    day: date | None = None
    savetime: datetime | None = None
    data: dict | None = None
