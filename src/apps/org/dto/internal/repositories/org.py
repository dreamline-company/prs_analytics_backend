from shared.dto.repositories import RepositoryDTO


class CreateOrgDTO(RepositoryDTO):
    abai_id: int
    parent_id: int | None = None
    name_ru: str
    name_ru_short: str | None = None
    org_type_id: int


class UpdateOrgDTO(RepositoryDTO):
    parent_id: int | None = None
    name_ru: str | None = None
    name_ru_short: str | None = None
    org_type_id: int | None = None
