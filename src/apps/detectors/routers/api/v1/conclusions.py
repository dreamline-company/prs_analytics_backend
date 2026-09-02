from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.conclusion import (
    ConclusionFeedbackDTO,
    DetectorConclusionDTO,
    WellAiConclusionDTO,
)
from apps.detectors.dto.requests.conclusion import ConclusionFeedbackRequestDTO
from apps.detectors.dto.responses.conclusion import (
    ConclusionFeedbackResponseDTO,
    ConclusionHistoryResponseDTO,
    WellAiConclusionResponseDTO,
)
from apps.detectors.repositories import (
    DetectorConclusionFeedbackRepository,
    DetectorConclusionRepository,
    DetectorIncidentRepository,
    DetectorRepository,
)
from apps.detectors.use_cases.get_well_ai_conclusion import (
    GetWellAiConclusionUseCase,
)
from apps.detectors.use_cases.list_well_conclusion_history import (
    ListWellConclusionHistoryUseCase,
)
from apps.detectors.use_cases.submit_conclusion_feedback import (
    SubmitConclusionFeedbackUseCase,
)
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/conclusions", tags=["detectors"])


@router.get(
    "/well/{well_id}",
    response_model=AppResponse[WellAiConclusionDTO],
)
async def get_well_ai_conclusion(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Path(ge=1, description="Well ID (wells_well.id)")],
) -> WellAiConclusionResponseDTO:
    """Актуальное ИИ-заключение скважины: по активным эпизодам R2/R9.

    ``primary`` — заключение эпизода худшего уровня, ``others`` — остальных
    активных; ``primary=null`` — активных эпизодов нет.
    """
    use_case = GetWellAiConclusionUseCase(
        incident_repository=DetectorIncidentRepository(session=session),
        conclusion_repository=DetectorConclusionRepository(session=session),
        detector_repository=DetectorRepository(session=session),
    )
    return WellAiConclusionResponseDTO(data=await use_case.execute(well_id))


@router.get(
    "/well/{well_id}/history",
    response_model=AppResponse[list[DetectorConclusionDTO]],
)
async def get_well_ai_conclusion_history(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Path(ge=1, description="Well ID (wells_well.id)")],
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ConclusionHistoryResponseDTO:
    """История заключений скважины: свежие сверху, включая failed/pending."""
    use_case = ListWellConclusionHistoryUseCase(
        conclusion_repository=DetectorConclusionRepository(session=session),
        detector_repository=DetectorRepository(session=session),
    )
    history = await use_case.execute(well_id=well_id, limit=limit, offset=offset)
    return ConclusionHistoryResponseDTO(data=history)


@router.post(
    "/{conclusion_id}/feedback",
    response_model=AppResponse[ConclusionFeedbackDTO],
)
async def submit_conclusion_feedback(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    conclusion_id: Annotated[int, Path(ge=1)],
    request: ConclusionFeedbackRequestDTO,
) -> ConclusionFeedbackResponseDTO:
    """Оценить заключение: звёзды 1–5 + обязательный комментарий технолога."""
    use_case = SubmitConclusionFeedbackUseCase(
        conclusion_repository=DetectorConclusionRepository(session=session),
        feedback_repository=DetectorConclusionFeedbackRepository(session=session),
    )
    feedback = await use_case.execute(
        conclusion_id=conclusion_id,
        rating=request.rating,
        comment=request.comment,
    )
    return ConclusionFeedbackResponseDTO(data=feedback)
