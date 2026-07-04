from starlette import status

from apps.files.dto.internal.file import FileDownloadDTO
from apps.files.dto.queries.file import GetFileByIdQuery
from apps.files.errors import FileMissingError
from apps.files.services.file import FileService
from shared.errors import HttpError


class FileNotFoundHttpError(HttpError):
    message = "File not found."
    code = "file_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetFileByIdUseCase:
    def __init__(self, file_service: FileService) -> None:
        self.file_service = file_service

    async def execute(self, query: GetFileByIdQuery) -> FileDownloadDTO:
        try:
            return await self.file_service.get_download_info(
                file_id=query.file_id,
                expires_in=query.expires_in,
            )
        except FileMissingError as exc:
            raise FileNotFoundHttpError(details={"file_id": query.file_id}) from exc
