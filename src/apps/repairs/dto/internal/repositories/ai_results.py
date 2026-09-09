import datetime

from shared.dto.repositories import RepositoryDTO


class _AIResultBaseCreateDTO(RepositoryDTO):
    status: str = "pending"
    model_name: str | None = None
    prompt_version: str | None = None
    result: dict | None = None
    error: str | None = None
    processed_at: datetime.datetime | None = None


class _AIResultBaseUpdateDTO(RepositoryDTO):
    status: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None
    result: dict | None = None
    error: str | None = None
    processed_at: datetime.datetime | None = None


class CreateRepairDynamogramAIResultDTO(_AIResultBaseCreateDTO):
    dynamogram_id: int


class UpdateRepairDynamogramAIResultDTO(_AIResultBaseUpdateDTO):
    pass


class CreateRepairSPOAIResultDTO(_AIResultBaseCreateDTO):
    spo_id: int


class UpdateRepairSPOAIResultDTO(_AIResultBaseUpdateDTO):
    pass


class CreateRepairAIAnalysisDTO(_AIResultBaseCreateDTO):
    analytics_id: int
    inputs_fingerprint: str | None = None


class UpdateRepairAIAnalysisDTO(_AIResultBaseUpdateDTO):
    inputs_fingerprint: str | None = None
