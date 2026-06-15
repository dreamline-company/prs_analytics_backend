from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateTelemetryDTO(RepositoryDTO):
    abai_id: int
    well_id: int
    date_time: datetime
    qv_liquid: float
    qm_oil: float


class UpdateTelemetryDTO(RepositoryDTO):
    well_id: int | None = None
    date_time: datetime | None = None
    qv_liquid: float | None = None
    qm_oil: float | None = None
