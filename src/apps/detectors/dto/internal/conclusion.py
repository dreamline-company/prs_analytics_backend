from datetime import datetime

from pydantic import BaseModel, ConfigDict


class ConclusionRecommendationDTO(BaseModel):
    """Шаг рекомендации из справочника — копия на момент генерации."""

    step: int
    text: str
    role: str
    deadline_hours: int
    priority: str
    priority_ru: str


class DetectorConclusionDTO(BaseModel):
    """ИИ-заключение для чтения наружу — строка ``detectors_conclusion``.

    ``detector_name_ru`` подставляется на чтении из реестра правил.
    ``status`` — pending (генерится) / completed / failed: фронт показывает
    незавершённое заключение как «формируется», summary там ещё NULL.
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    incident_id: int
    well_id: int
    detector_code: str
    detector_name_ru: str | None = None
    reason_code: str
    level: str
    status: str
    cause: str
    recommendations: list[ConclusionRecommendationDTO]
    confidence: float
    summary: str | None
    model_name: str | None
    prompt_version: str
    created_at: datetime
    updated_at: datetime


class WellAiConclusionDTO(BaseModel):
    """Заключение скважины: главное (худший уровень) + остальные активные."""

    primary: DetectorConclusionDTO | None
    others: list[DetectorConclusionDTO]


class ConclusionFeedbackDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    conclusion_id: int
    rating: int
    comment: str
    created_at: datetime
