from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.telemetry.dto.internal.sdmo import SdmoParametersDTO
from apps.telemetry.dto.queries.sdmo import ListSdmoParametersByWellIdQuery
from apps.telemetry.dto.responses.sdmo import ListSdmoParametersResponseDTO
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.use_cases.list_sdmo_parameters_by_well_id import (
    ListSdmoParametersByWellIdUseCase,
)
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/sdmo-parameters", tags=["sdmo"])


@router.get("", response_model=AppResponse[list[SdmoParametersDTO]])
async def get_sdmo_parameters(
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
) -> ListSdmoParametersResponseDTO:
    use_case = ListSdmoParametersByWellIdUseCase(
        sdmo_station_repository=SdmoStationRepository(session=session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
        sdmo_fc_reg_repository=SdmoFcRegRepository(session=session),
    )
    parameters = await use_case.execute(
        ListSdmoParametersByWellIdQuery(
            well_id=well_id,
            start_time=start_time,
            end_time=end_time,
        ),
    )
    return ListSdmoParametersResponseDTO(data=parameters)
