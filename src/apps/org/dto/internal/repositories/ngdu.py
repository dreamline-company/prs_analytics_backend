from shared.dto.repositories import RepositoryDTO


class CreateNGDUDTO(RepositoryDTO):
    name: str


class UpdateNGDUDTO(RepositoryDTO):
    name: str | None = None
