from datetime import date

from shared.dto.repositories import RepositoryDTO


class CreateTechRegimeDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    start_date: date
    end_date: date
    liquid: float | None
    oil: float | None


class UpdateTechRegimeDTO(RepositoryDTO):
    abai_well_id: int | None = None
    start_date: date | None = None
    end_date: date | None = None
    liquid: float | None = None
    oil: float | None = None
