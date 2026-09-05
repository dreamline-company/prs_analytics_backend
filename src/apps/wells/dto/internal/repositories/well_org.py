from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateWellOrgDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    abai_org_id: int
    dbeg: date | None = None
    dend: date | None = None


class UpdateWellOrgDTO(RepositoryDTO):
    abai_well_id: int | None = None
    abai_org_id: int | None = None
    dbeg: date | None = None
    dend: date | None = None
