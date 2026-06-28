from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.queries.well import SearchWellsByNameQuery
from apps.wells.dto.responses.well import SearchWellsResponseDTO
from apps.wells.repositories.well import WellRepository
from apps.wells.use_cases.search_wells_by_name import SearchWellsByNameUseCase
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

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
