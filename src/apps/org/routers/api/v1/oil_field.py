from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.dto.internal.oil_field import OilFieldDTO
from apps.org.dto.queries.oil_field import ListOilFieldsQuery
from apps.org.dto.responses.oil_field import ListOilFieldsResponseDTO
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.use_cases.list_oil_fields import ListOilFieldsUseCase
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/oil-fields", tags=["oil-fields"])


@router.get("", response_model=AppResponse[list[OilFieldDTO]])
async def list_oil_fields(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: Annotated[
        int | None,
        Query(ge=1, description="Optional local Org.id of the NGDU to filter by"),
    ] = None,
) -> ListOilFieldsResponseDTO:
    use_case = ListOilFieldsUseCase(
        oil_field_repository=OilFieldRepository(session=session),
    )
    oil_fields = await use_case.execute(ListOilFieldsQuery(ngdu_id=ngdu_id))
    return ListOilFieldsResponseDTO(data=oil_fields)
