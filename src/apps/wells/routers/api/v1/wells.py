from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.dto.internal.well import WellMatrixItemDTO, WellShortDTO
from apps.wells.dto.queries.well import GetWellsMatrixQuery, SearchWellsByNameQuery
from apps.wells.dto.responses.well import (
    SearchWellsResponseDTO,
    WellsMatrixResponseDTO,
)
from apps.wells.repositories.well import WellRepository
from apps.wells.use_cases.get_wells_matrix import GetWellsMatrixUseCase
from apps.wells.use_cases.search_wells_by_name import SearchWellsByNameUseCase
from shared.dependencies.db import get_abai_session, get_app_session
from shared.dto.api import AppResponse
from shared.integrations.abai.repositories.well_orgs import ABAIWellOrgRepository

router = APIRouter(prefix="/wells", tags=["wells"])


@router.get("/search", response_model=AppResponse[list[WellShortDTO]])
async def search_wells_by_name(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    name: Annotated[
        str,
        Query(min_length=1, max_length=15, description="Well name substring"),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchWellsResponseDTO:
    use_case = SearchWellsByNameUseCase(
        well_repository=WellRepository(session=session),
    )
    wells = await use_case.execute(
        SearchWellsByNameQuery(name=name, limit=limit),
    )
    return SearchWellsResponseDTO(data=wells)


@router.get("/matrix", response_model=AppResponse[list[WellMatrixItemDTO]])
async def get_wells_matrix(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    abai_session: Annotated[AsyncSession, Depends(get_abai_session)],
    ngdu_id: Annotated[
        int,
        Query(ge=1, description="Local Org.id of the NGDU to filter wells by"),
    ],
) -> WellsMatrixResponseDTO:
    use_case = GetWellsMatrixUseCase(
        org_repository=OrgRepository(session=app_session),
        well_repository=WellRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        abai_well_org_repository=ABAIWellOrgRepository(session=abai_session),
    )
    matrix = await use_case.execute(GetWellsMatrixQuery(ngdu_id=ngdu_id))
    return WellsMatrixResponseDTO(data=matrix)
