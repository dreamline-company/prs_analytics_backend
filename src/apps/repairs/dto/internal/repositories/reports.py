from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateRepairSummaryDTO(RepositoryDTO):
    repair_id: int | None = None
    well_id: int
    date: date


class UpdateRepairSummaryDTO(RepositoryDTO):
    repair_id: int | None = None
    well_id: int | None = None
    date: date | None = None
