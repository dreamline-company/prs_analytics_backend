from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.telemetry.dto.internal.telemetry import TelemetryDTO
from apps.telemetry.dto.queries.telemetry import ListTelemetryByWellIdQuery
from apps.telemetry.dto.responses.telemetry import ListTelemetryResponseDTO
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.use_cases.list_telemetry_by_well_id import (
    ListTelemetryByWellIdUseCase,
)
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/telemetry", tags=["telemetry"])


@router.get("", response_model=AppResponse[list[TelemetryDTO]])
async def list_telemetry_by_well_id(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID")],
    date_time_from: Annotated[
        datetime | None,
        Query(description="Filter: Telemetry.date_time >= date_time_from"),
    ] = None,
    date_time_to: Annotated[
        datetime | None,
        Query(description="Filter: Telemetry.date_time <= date_time_to"),
    ] = None,
) -> ListTelemetryResponseDTO:
    use_case = ListTelemetryByWellIdUseCase(
        telemetry_repository=TelemetryRepository(session=session),
        well_repository=WellRepository(session=session),
    )
    telemetry = await use_case.execute(
        ListTelemetryByWellIdQuery(
            well_id=well_id,
            date_time_from=date_time_from,
            date_time_to=date_time_to,
        ),
    )
    return ListTelemetryResponseDTO(data=telemetry)
