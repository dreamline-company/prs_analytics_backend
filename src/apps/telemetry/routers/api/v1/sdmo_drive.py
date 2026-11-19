from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from apps.telemetry.dto.internal.sdmo import SdmoDriveStationDTO
from apps.telemetry.dto.queries.sdmo import ListSdmoDriveSegmentsByWellIdQuery
from apps.telemetry.dto.responses.sdmo import ListSdmoDriveSegmentsResponseDTO
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository, SdmoStationRepository
from apps.telemetry.use_cases.list_sdmo_drive_segments_by_well_id import (
    ListSdmoDriveSegmentsByWellIdUseCase,
    to_local_naive,
)
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/sdmo-drive-segments", tags=["sdmo"])


@router.get("", response_model=AppResponse[list[SdmoDriveStationDTO]])
async def get_sdmo_drive_segments(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID (wells_well.id)")],
    start_time: Annotated[
        datetime | None,
        Query(description="Filter: SdmoFcData.savetime >= start_time (datetime/date)"),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Query(description="Filter: SdmoFcData.savetime <= end_time (datetime/date)"),
    ] = None,
    no_data_gap_minutes: Annotated[
        int,
        Query(
            ge=1,
            le=24 * 60,
            description="Gap without usable status packets that becomes no_data",
        ),
    ] = 30,
) -> ListSdmoDriveSegmentsResponseDTO:
    """Отрезки состояния привода по регистру 1999 (подряд идущие отсчёты с
    одним кодом) по станциям скважины — пуски и остановки строит фронт."""
    start_time, end_time = to_local_naive(start_time), to_local_naive(end_time)
    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="start_time must not be later than end_time",
        )
    use_case = ListSdmoDriveSegmentsByWellIdUseCase(
        sdmo_station_repository=SdmoStationRepository(session=session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
    )
    stations = await use_case.execute(
        ListSdmoDriveSegmentsByWellIdQuery(
            well_id=well_id,
            start_time=start_time,
            end_time=end_time,
            no_data_gap_minutes=no_data_gap_minutes,
        ),
    )
    return ListSdmoDriveSegmentsResponseDTO(data=stations)
