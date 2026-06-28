from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.repairs.dto.internal.repair import RepairDTO
from apps.repairs.dto.queries.repair import ListRepairsByWellIdQuery
from apps.repairs.dto.responses.repair import ListRepairsResponseDTO
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.use_cases.list_repairs_by_well_id import (
    ListRepairsByWellIdUseCase,
)
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/repairs", tags=["repairs"])


@router.get("", response_model=AppResponse[list[RepairDTO]])
async def list_repairs_by_well_id(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID")],
) -> ListRepairsResponseDTO:
    use_case = ListRepairsByWellIdUseCase(
        repair_repository=RepairRepository(session=session),
        well_repository=WellRepository(session=session),
    )
    repairs = await use_case.execute(
        ListRepairsByWellIdQuery(well_id=well_id),
    )
    return ListRepairsResponseDTO(data=repairs)
