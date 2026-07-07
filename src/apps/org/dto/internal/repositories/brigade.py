from shared.dto.repositories import RepositoryDTO


class CreateBrigadeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str
    name_ru_short: str | None = None
    own: bool | None = None
    org_id: int | None = None


class UpdateBrigadeDTO(RepositoryDTO):
    name_ru: str | None = None
    name_ru_short: str | None = None
    own: bool | None = None
    org_id: int | None = None


class CreateUniqueBrigadeDTO(RepositoryDTO):
    name: str
    ngdu_id: int


class UpdateUniqueBrigadeDTO(RepositoryDTO):
    name: str | None = None
    ngdu_id: int | None = None
