from shared.dto.repositories import RepositoryDTO


class CreateWellDTO(RepositoryDTO):
    abai_id: int
    name: str
    coords_id: int | None = None


class UpdateWellDTO(RepositoryDTO):
    name: str | None = None
    coords_id: int | None = None
