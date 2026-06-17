from shared.dto.repositories import RepositoryDTO


class CreateFileDTO(RepositoryDTO):
    file: str


class UpdateFileDTO(RepositoryDTO):
    file: str | None = None
