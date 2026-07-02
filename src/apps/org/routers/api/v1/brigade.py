from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.dto.internal.brigade import BrigadeDTO
from apps.org.dto.queries.brigade import ListBrigadesByNGDUIdQuery
from apps.org.dto.responses.brigade import ListBrigadesResponseDTO
from apps.org.use_cases.list_brigades_by_ngdu_id import ListBrigadesByNGDUIdUseCase
from shared.dependencies.db import get_cm_session
from shared.dto.api import AppResponse
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

router = APIRouter(prefix="/brigades", tags=["brigades"])


@router.get("", response_model=AppResponse[list[BrigadeDTO]])
async def list_brigades_by_ngdu_id(
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    ngdu_id: Annotated[int, Query(ge=1, description="NGDU ID (filter)")],
) -> ListBrigadesResponseDTO:
    use_case = ListBrigadesByNGDUIdUseCase(
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
    )
    brigades = await use_case.execute(
        ListBrigadesByNGDUIdQuery(ngdu_id=ngdu_id),
    )
    return ListBrigadesResponseDTO(data=brigades)
