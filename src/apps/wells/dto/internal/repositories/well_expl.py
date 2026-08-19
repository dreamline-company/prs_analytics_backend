from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateWellExplTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str | None = None
    name_short_ru: str | None = None
    tbd_id: int | None = None
    code: str | None = None


class UpdateWellExplTypeDTO(RepositoryDTO):
    name_ru: str | None = None
    name_short_ru: str | None = None
    tbd_id: int | None = None
    code: str | None = None


class CreateWellExplDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    expl: int | None = None
    dbeg: date | None = None
    dend: date | None = None


class UpdateWellExplDTO(RepositoryDTO):
    abai_well_id: int | None = None
    expl: int | None = None
    dbeg: date | None = None
    dend: date | None = None
