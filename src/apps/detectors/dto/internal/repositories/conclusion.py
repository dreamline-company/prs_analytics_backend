from shared.dto.repositories import RepositoryDTO


class CreateConclusionDTO(RepositoryDTO):
    incident_id: int
    well_id: int
    detector_code: str
    reason_code: str
    level: str
    status: str
    cause: str
    recommendations: list
    confidence: float
    summary: str | None = None
    error: str | None = None
    model_name: str | None = None
    prompt_version: str


class UpdateConclusionDTO(RepositoryDTO):
    status: str | None = None
    cause: str | None = None
    recommendations: list | None = None
    confidence: float | None = None
    summary: str | None = None
    error: str | None = None
    model_name: str | None = None
    prompt_version: str | None = None


class CreateConclusionFeedbackDTO(RepositoryDTO):
    conclusion_id: int
    rating: int
    comment: str


class UpdateConclusionFeedbackDTO(RepositoryDTO):
    rating: int | None = None
    comment: str | None = None
