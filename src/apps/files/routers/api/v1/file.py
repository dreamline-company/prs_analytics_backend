from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.dto.internal.file import FileDownloadDTO
from apps.files.dto.queries.file import GetFileByIdQuery
from apps.files.dto.responses.file import FileDownloadResponseDTO
from apps.files.services.file import FileService
from apps.files.use_cases.get_file_by_id import GetFileByIdUseCase
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import get_aioboto_client_factory, get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/files", tags=["files"])
settings = get_settings()


@router.get("/{file_id}", response_model=AppResponse[FileDownloadDTO])
async def get_file_by_id(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    file_id: Annotated[int, Path(ge=1, description="File ID")],
    expires_in: Annotated[
        int,
        Query(
            ge=60,
            le=7 * 24 * 3600,
            description="Presigned URL lifetime, seconds (60..604800). Default 1h.",
        ),
    ] = 3600,
) -> FileDownloadResponseDTO:
    # Uploads currently land in REPORTS_BUCKET_NAME (see fill_repair_analytics,
    # upload_parsed_summaries). If buckets diverge later, add a bucket column
    # to File and pass it through here.
    storage = AiobotoFileStorage(
        bucket_name=settings.PRS_REPAIRS_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
    )
    file_service = FileService(session=session, storage=storage)
    use_case = GetFileByIdUseCase(file_service=file_service)
    download = await use_case.execute(
        GetFileByIdQuery(file_id=file_id, expires_in=expires_in),
    )
    return FileDownloadResponseDTO(data=download)
