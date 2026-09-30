from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.dto.internal.ngdu import NGDUShortDTO
from apps.org.dto.queries.ngdu import SearchNGDUByNameQuery
from apps.org.dto.responses.ngdu import ListNGDUResponseDTO, SearchNGDUResponseDTO
from apps.org.repositories.org import OrgRepository
from apps.org.use_cases.list_ngdus import PRS_NGDU_ABAI_IDS, ListNGDUsUseCase
from apps.org.use_cases.search_ngdu_by_name import SearchNGDUByNameUseCase
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/ngdu", tags=["ngdu"])


@router.get("", response_model=AppResponse[list[NGDUShortDTO]])
async def list_ngdus(
    session: Annotated[AsyncSession, Depends(get_app_session)],
) -> ListNGDUResponseDTO:
    use_case = ListNGDUsUseCase(
        org_repository=OrgRepository(session=session),
    )
    ngdus = await use_case.execute()
    return ListNGDUResponseDTO(data=ngdus)


@router.get("/prs", response_model=AppResponse[list[NGDUShortDTO]])
async def list_ngdus_prs(
    session: Annotated[AsyncSession, Depends(get_app_session)],
) -> ListNGDUResponseDTO:
    """НГДУ для экранов ПРС: только Кайнармунайгаз и Жылыоймунайгаз."""
    use_case = ListNGDUsUseCase(
        org_repository=OrgRepository(session=session),
        abai_ids=PRS_NGDU_ABAI_IDS,
    )
    ngdus = await use_case.execute()
    return ListNGDUResponseDTO(data=ngdus)


@router.get("/search", response_model=AppResponse[list[NGDUShortDTO]])
async def search_ngdu_by_name(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    name: Annotated[
        str,
        Query(min_length=1, max_length=30, description="NGDU name substring"),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchNGDUResponseDTO:
    use_case = SearchNGDUByNameUseCase(
        org_repository=OrgRepository(session=session),
    )
    ngdus = await use_case.execute(
        SearchNGDUByNameQuery(name=name, limit=limit),
    )
    return SearchNGDUResponseDTO(data=ngdus)
