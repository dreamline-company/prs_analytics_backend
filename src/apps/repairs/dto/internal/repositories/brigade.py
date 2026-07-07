from shared.dto.repositories import RepositoryDTO


class CreateRepairBrigadeDTO(RepositoryDTO):
    repair_id: int
    brigade_id: int


class UpdateRepairBrigadeDTO(RepositoryDTO):
    repair_id: int | None = None
    brigade_id: int | None = None
