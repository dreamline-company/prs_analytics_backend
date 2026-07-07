from shared.dto.repositories import RepositoryDTO


class CreateOrgTypeDTO(RepositoryDTO):
    abai_id: int


class UpdateOrgTypeDTO(RepositoryDTO):
    abai_id: int | None = None
