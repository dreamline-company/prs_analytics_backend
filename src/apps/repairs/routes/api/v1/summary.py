from fastapi import APIRouter
from starlette import status

from apps.repairs.dto.requests.summaries import UploadParsedSummariesListDTO

router = APIRouter(prefix="summaries", tags=["summaries"])


@router.post("/parsed", status_code=status.HTTP_200_OK)
async def upload_parsed_xlsx_summary(summaries: UploadParsedSummariesListDTO) -> None:
    return None
