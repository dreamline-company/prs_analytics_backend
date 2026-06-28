from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.repairs.dto.internal.summary import RepairSummaryDTO
from apps.repairs.dto.queries.summary import ListRepairSummariesByRepairIdQuery
from apps.repairs.dto.requests.summaries import UploadParsedSummariesListDTO
from apps.repairs.dto.responses.summary import ListRepairSummariesResponseDTO
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.use_cases.list_repair_summaries_by_repair_id import (
    ListRepairSummariesByRepairIdUseCase,
)
from apps.repairs.use_cases.upload_parsed_summaries import (
    UploadParsedSummariesUseCase,
)
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/summaries", tags=["summaries"])


@router.get("", response_model=AppResponse[list[RepairSummaryDTO]])
async def list_repair_summaries_by_repair_id(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    repair_id: Annotated[int, Query(ge=1, description="Repair ID")],
) -> ListRepairSummariesResponseDTO:
    use_case = ListRepairSummariesByRepairIdUseCase(
        repair_summary_repository=RepairSummaryRepository(session=session),
    )
    summaries = await use_case.execute(
        ListRepairSummariesByRepairIdQuery(repair_id=repair_id),
    )
    return ListRepairSummariesResponseDTO(data=summaries)


@router.post("/parsed", status_code=status.HTTP_200_OK)
async def upload_parsed_xlsx_summary(
    summaries: UploadParsedSummariesListDTO,
    session: Annotated[AsyncSession, Depends(get_app_session)],
) -> dict[str, int]:
    use_case = UploadParsedSummariesUseCase(
        session=session,
        well_repository=WellRepository(session=session),
        repair_summary_repository=RepairSummaryRepository(session=session),
    )
    created = await use_case.execute(summaries)
    return {"created": created}
