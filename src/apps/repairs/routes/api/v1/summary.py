from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.repairs.dto.requests.summaries import UploadParsedSummariesListDTO
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.use_cases.upload_parsed_summaries import (
    UploadParsedSummariesUseCase,
)
from apps.wells.repositories import WellRepository
from shared.dependencies.db import get_app_session

router = APIRouter(prefix="/summaries", tags=["summaries"])


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
