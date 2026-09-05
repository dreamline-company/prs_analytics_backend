from shared.dto.repositories import RepositoryDTO


class CreateOilFieldDTO(RepositoryDTO):
    prefix: str
    name: str
    ngdu_id: int


class UpdateOilFieldDTO(RepositoryDTO):
    prefix: str | None = None
    name: str | None = None
    ngdu_id: int | None = None
