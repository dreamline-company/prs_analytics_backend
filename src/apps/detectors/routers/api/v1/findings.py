from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.finding import DetectorFindingDTO
from apps.detectors.dto.queries.finding import FindingSection, ListFindingsQuery
from apps.detectors.dto.responses.finding import ListDetectorFindingsResponseDTO
from apps.detectors.repositories import DetectorFindingRepository
from apps.detectors.use_cases.list_findings import ListFindingsUseCase
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/findings", tags=["detectors"])


@router.get("", response_model=AppResponse[list[DetectorFindingDTO]])
async def get_detector_findings(  # noqa: PLR0913
    session: Annotated[AsyncSession, Depends(get_app_session)],
    detector_code: Annotated[
        str,
        Query(description="Detector code; findings exist for R10 only"),
    ] = "R10",
    fix_date: Annotated[
        date | None,
        Query(description="Fix date (closed local day); default — the latest one"),
    ] = None,
    section: Annotated[
        FindingSection | None,
        Query(
            description=(
                "measure_request — «замер устарел, запросить замер»; "
                "data_quality — пустые замеры и сервисный перечень"
            ),
        ),
    ] = None,
    kind: Annotated[
        str | None,
        Query(description="Filter: finding code (stale, chronic_low, ...)"),
    ] = None,
    well_id: Annotated[
        int | None,
        Query(ge=1, description="Filter: well ID (wells_well.id)"),
    ] = None,
) -> ListDetectorFindingsResponseDTO:
    use_case = ListFindingsUseCase(
        finding_repository=DetectorFindingRepository(session=session),
        well_repository=WellRepository(session=session),
    )
    findings = await use_case.execute(
        ListFindingsQuery(
            detector_code=detector_code,
            fix_date=fix_date,
            section=section,
            kind=kind,
            well_id=well_id,
        ),
    )
    return ListDetectorFindingsResponseDTO(data=findings)
