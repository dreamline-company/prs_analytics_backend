from shared.dto.repositories import RepositoryDTO


class CreateRepairAnalyticsDTO(RepositoryDTO):
    repair_id: int
    summary_id: int | None = None
    repair_docs_id: int | None = None
    is_finalized: bool = False


class UpdateRepairAnalyticsDTO(RepositoryDTO):
    summary_id: int | None = None
    repair_docs_id: int | None = None
    is_finalized: bool | None = None


class CreateRepairAnalyticsDynamogramDTO(RepositoryDTO):
    analytics_id: int
    dynamogram_before_id: int | None = None
    dynamogram_after_id: int | None = None


class UpdateRepairAnalyticsDynamogramDTO(RepositoryDTO):
    dynamogram_before_id: int | None = None
    dynamogram_after_id: int | None = None


class CreateRepairAnalyticsSPODTO(RepositoryDTO):
    analytics_id: int
    spo_id: int


class UpdateRepairAnalyticsSPODTO(RepositoryDTO):
    spo_id: int | None = None
