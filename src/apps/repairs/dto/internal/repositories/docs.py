from shared.dto.repositories import RepositoryDTO


class CreateRepairDocDTO(RepositoryDTO):
    repair_id: int | None = None
    act_file_id: int | None = None
    por_file_id: int | None = None
    source_hash: str | None = None


class UpdateRepairDocDTO(RepositoryDTO):
    repair_id: int | None = None
    act_file_id: int | None = None
    por_file_id: int | None = None
    source_hash: str | None = None
