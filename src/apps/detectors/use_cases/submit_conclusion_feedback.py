"""Оценка ИИ-заключения технологом: звёзды + обязательный комментарий."""

from starlette import status

from apps.detectors.dto.internal.conclusion import ConclusionFeedbackDTO
from apps.detectors.dto.internal.repositories.conclusion import (
    CreateConclusionFeedbackDTO,
)
from apps.detectors.models.conclusion import DetectorConclusion
from apps.detectors.repositories import (
    DetectorConclusionFeedbackRepository,
    DetectorConclusionRepository,
)
from shared.errors import HttpError
from shared.repository.sqlalchemy import QuerySpec


class ConclusionNotFoundError(HttpError):
    message = "Conclusion not found."
    code = "conclusion_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class SubmitConclusionFeedbackUseCase:
    """Пишет отзыв и коммитит: транзакция — зона ответственности сценария."""

    def __init__(
        self,
        *,
        conclusion_repository: DetectorConclusionRepository,
        feedback_repository: DetectorConclusionFeedbackRepository,
    ) -> None:
        self.conclusion_repository = conclusion_repository
        self.feedback_repository = feedback_repository

    async def execute(
        self,
        *,
        conclusion_id: int,
        rating: int,
        comment: str,
    ) -> ConclusionFeedbackDTO:
        conclusion = await self.conclusion_repository.get_one(
            QuerySpec(filters=(DetectorConclusion.id == conclusion_id,)),
        )
        if conclusion is None:
            raise ConclusionNotFoundError(details={"conclusion_id": conclusion_id})

        feedback = await self.feedback_repository.create(
            CreateConclusionFeedbackDTO(
                conclusion_id=conclusion_id,
                rating=rating,
                comment=comment,
            ),
        )
        await self.feedback_repository.session.commit()
        return ConclusionFeedbackDTO.model_validate(feedback)
