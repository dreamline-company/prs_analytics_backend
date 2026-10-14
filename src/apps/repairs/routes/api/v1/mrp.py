from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.repairs.dto.internal.mrp import WellMrpDTO
from apps.repairs.dto.queries.mrp import GetWellMrpQuery
from apps.repairs.dto.responses.mrp import WellMrpResponseDTO
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.use_cases.get_well_mrp import GetWellMrpUseCase
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/mrp", tags=["repairs"])


@router.get("/by-well", response_model=AppResponse[WellMrpDTO])
async def get_well_mrp(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID")],
) -> WellMrpResponseDTO:
    """МРП скважины: до/после каждого ремонта, индикатор качества, среднее."""
    use_case = GetWellMrpUseCase(
        repair_repository=RepairRepository(session=session),
        well_repository=WellRepository(session=session),
    )
    mrp = await use_case.execute(GetWellMrpQuery(well_id=well_id))
    return WellMrpResponseDTO(data=mrp)
