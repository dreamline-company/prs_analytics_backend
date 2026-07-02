from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.dto.internal.ngdu import NGDUShortDTO
from apps.org.dto.queries.ngdu import SearchNGDUByNameQuery
from apps.org.dto.responses.ngdu import SearchNGDUResponseDTO
from apps.org.use_cases.search_ngdu_by_name import SearchNGDUByNameUseCase
from shared.dependencies.db import get_cm_session
from shared.dto.api import AppResponse
from shared.integrations.cm.repositories.ngdu import CMNGDURepository

router = APIRouter(prefix="/ngdu", tags=["ngdu"])


@router.get("/search", response_model=AppResponse[list[NGDUShortDTO]])
async def search_ngdu_by_name(
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    name: Annotated[
        str,
        Query(min_length=1, max_length=30, description="NGDU name substring"),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchNGDUResponseDTO:
    use_case = SearchNGDUByNameUseCase(
        cm_ngdu_repository=CMNGDURepository(session=cm_session),
    )
    ngdus = await use_case.execute(
        SearchNGDUByNameQuery(name=name, limit=limit),
    )
    return SearchNGDUResponseDTO(data=ngdus)
