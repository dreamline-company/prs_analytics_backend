from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.telemetry.dto.internal.tech_regime import TechRegimeDTO
from apps.telemetry.dto.queries.tech_regime import ListTechRegimeByWellIdQuery
from apps.telemetry.dto.responses.tech_regime import ListTechRegimeResponseDTO
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.use_cases.list_tech_regime_by_well_id import (
    ListTechRegimeByWellIdUseCase,
)
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/tech-regime", tags=["tech-regime"])


@router.get("", response_model=AppResponse[list[TechRegimeDTO]])
async def list_tech_regime_by_well_id(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID")],
    start_date_from: Annotated[
        date | None,
        Query(description="Filter: TechRegime.start_date >= start_date_from"),
    ] = None,
    start_date_to: Annotated[
        date | None,
        Query(description="Filter: TechRegime.start_date <= start_date_to"),
    ] = None,
) -> ListTechRegimeResponseDTO:
    use_case = ListTechRegimeByWellIdUseCase(
        tech_regime_repository=TechRegimeRepository(session=session),
        well_repository=WellRepository(session=session),
    )
    regimes = await use_case.execute(
        ListTechRegimeByWellIdQuery(
            well_id=well_id,
            start_date_from=start_date_from,
            start_date_to=start_date_to,
        ),
    )
    return ListTechRegimeResponseDTO(data=regimes)
