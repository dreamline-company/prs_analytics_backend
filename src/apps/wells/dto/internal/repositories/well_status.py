from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateWellStatusTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str
    code: str | None = None
    name_short_ru: str | None = None
    tbd_id: int | None = None


class UpdateWellStatusTypeDTO(RepositoryDTO):
    name_ru: str | None = None
    code: str | None = None
    name_short_ru: str | None = None
    tbd_id: int | None = None


class CreateWellStatusReasonDTO(RepositoryDTO):
    abai_id: int
    reason_type: int
    name_ru: str
    code: str | None = None
    parent: int | None = None
    name_short_ru: str | None = None


class UpdateWellStatusReasonDTO(RepositoryDTO):
    reason_type: int | None = None
    name_ru: str | None = None
    code: str | None = None
    parent: int | None = None
    name_short_ru: str | None = None


class CreateWellStatusDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    status: int
    reason: int | None = None
    dbeg: datetime
    dend: datetime


class UpdateWellStatusDTO(RepositoryDTO):
    abai_well_id: int | None = None
    status: int | None = None
    reason: int | None = None
    dbeg: datetime | None = None
    dend: datetime | None = None
