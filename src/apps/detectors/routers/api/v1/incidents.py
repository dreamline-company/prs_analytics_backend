from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.incident import DetectorIncidentDTO
from apps.detectors.dto.queries.incident import (
    IncidentLevel,
    IncidentStatus,
    IncidentVerdict,
    ListIncidentsByWellIdQuery,
)
from apps.detectors.dto.responses.incident import ListDetectorIncidentsResponseDTO
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from apps.detectors.use_cases.list_incidents_by_well_id import (
    ListIncidentsByWellIdUseCase,
)
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/incidents", tags=["detectors"])


@router.get("", response_model=AppResponse[list[DetectorIncidentDTO]])
async def get_detector_incidents(  # noqa: PLR0913
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID (wells_well.id)")],
    detector_code: Annotated[
        str | None,
        Query(description="Filter: detector code (R2, R9, ...)"),
    ] = None,
    reason_code: Annotated[
        str | None,
        Query(description="Filter: signal code (rod_break, load_imbalance, ...)"),
    ] = None,
    status: Annotated[
        IncidentStatus | None,
        Query(description="Filter: active | normalized"),
    ] = None,
    level: Annotated[
        IncidentLevel | None,
        Query(description="Filter: warning | alarm"),
    ] = None,
    verdict: Annotated[
        IncidentVerdict | None,
        Query(
            description=(
                "Filter: verification verdict — pending (incl. not yet verified) | "
                "false_alarm | failure_likely | failure_confirmed | undetermined"
            ),
        ),
    ] = None,
    opened_from: Annotated[
        datetime | None,
        Query(description="Filter: DetectorIncident.opened_at >= opened_from"),
    ] = None,
    opened_to: Annotated[
        datetime | None,
        Query(description="Filter: DetectorIncident.opened_at <= opened_to"),
    ] = None,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ListDetectorIncidentsResponseDTO:
    use_case = ListIncidentsByWellIdUseCase(
        incident_repository=DetectorIncidentRepository(session=session),
        detector_repository=DetectorRepository(session=session),
        verification_repository=DetectorVerificationRepository(session=session),
    )
    incidents = await use_case.execute(
        ListIncidentsByWellIdQuery(
            well_id=well_id,
            detector_code=detector_code,
            reason_code=reason_code,
            status=status,
            level=level,
            verdict=verdict,
            opened_from=opened_from,
            opened_to=opened_to,
            limit=limit,
            offset=offset,
        ),
    )
    return ListDetectorIncidentsResponseDTO(data=incidents)
