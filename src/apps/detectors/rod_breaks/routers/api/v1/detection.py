from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks.dto.queries.detection import (
    ListRodBreakDetectionsQuery,
)
from apps.detectors.rod_breaks.dto.responses.detection import (
    ListRodBreakDetectionsResponse,
)
from apps.detectors.rod_breaks.repositories.detection import (
    RodBreakDetectionRepository,
)
from apps.detectors.rod_breaks.use_cases.list_detections import (
    ListRodBreakDetections,
)
from shared.dependencies.db import get_app_session

router = APIRouter(prefix="/rod-breaks", tags=["detectors:rod_breaks"])


@router.get("/detections")
async def list_detections(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int | None, Query(ge=1, description="Well ID")] = None,
    only_fired: Annotated[  # noqa: FBT002
        bool,
        Query(description="Только сработавшие детекции"),
    ] = False,
    limit: Annotated[int, Query(ge=1, le=1000)] = 100,
) -> ListRodBreakDetectionsResponse:
    use_case = ListRodBreakDetections(RodBreakDetectionRepository(session))
    detections = await use_case.execute(
        ListRodBreakDetectionsQuery(
            well_id=well_id,
            only_fired=only_fired,
            limit=limit,
        ),
    )
    return ListRodBreakDetectionsResponse(data=detections)
