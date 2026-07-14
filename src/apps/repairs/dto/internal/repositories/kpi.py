import datetime

from shared.dto.repositories import RepositoryDTO


class CreateRepairKPIDTO(RepositoryDTO):
    analytics_id: int
    status: str = "pending"
    metrics: dict | None = None
    error: str | None = None
    computed_at: datetime.datetime | None = None


class UpdateRepairKPIDTO(RepositoryDTO):
    status: str | None = None
    metrics: dict | None = None
    error: str | None = None
    computed_at: datetime.datetime | None = None
