from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateTelemetryDTO(RepositoryDTO):
    well_id: int
    date_time: datetime
    qv_liquid: float | None
    qm_oil: float | None
    ngdu_id: int
    oil_field: str | None = None
    gzu: str | None = None
    otvod: int | None = None


class UpdateTelemetryDTO(RepositoryDTO):
    well_id: int | None = None
    date_time: datetime | None = None
    qv_liquid: float | None = None
    qm_oil: float | None = None
    ngdu_id: int | None = None
    oil_field: str | None = None
    gzu: str | None = None
    otvod: int | None = None
