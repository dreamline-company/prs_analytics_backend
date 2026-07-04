import datetime
from io import BytesIO

from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.dto.internal.file import FileDownloadDTO
from apps.files.dto.internal.repositories.file import CreateFileDTO
from apps.files.errors import FileMissingError, FileUploadConsistencyError
from apps.files.models.file import File
from apps.files.repositories.file import FileRepository
from core import get_logger
from shared.database.s3.storage import AiobotoFileStorage

logger = get_logger(__name__)


class FileService:
    """Manages file persistence with guaranteed DB↔S3 consistency.

    Upload strategy:
        1. Upload bytes to S3. On S3 error → raise immediately, DB untouched.
        2. Insert DB record and commit. On failure → rollback session and delete
           the already-uploaded S3 object as compensation.
           If compensation also fails → raise FileUploadConsistencyError so the
           caller knows manual cleanup is required.

    Delete strategy:
        1. Delete DB record and commit. DB is the source of truth.
        2. Delete S3 object. If S3 delete fails → log the orphaned key; the
           object is unreachable via the app and can be cleaned up later.
    """

    def __init__(
        self,
        session: AsyncSession,
        storage: AiobotoFileStorage,
    ) -> None:
        self._session = session
        self._storage = storage
        self._repo = FileRepository(session)

    async def upload(self, file: BytesIO, s3_key: str) -> File:
        """Upload *file* to S3 at *s3_key* and persist a DB record.

        Args:
            file: File bytes to upload.
            s3_key: Object key (path) inside the S3 bucket.

        Returns:
            Created :class:`~apps.files.models.file.File` ORM instance.

        Raises:
            shared.database.s3.storage.FileUploadError: If the S3 upload fails.
            FileUploadConsistencyError: If DB commit fails *and* S3 compensation
                also fails — manual S3 cleanup of *s3_key* is required.
        """
        await self._storage.upload_file(file, s3_key)

        try:
            db_file = await self._repo.create(CreateFileDTO(file=s3_key))
            await self._session.commit()
        except Exception:
            await self._session.rollback()
            logger.exception(
                "DB commit failed after S3 upload; compensating by deleting s3_key=%s",
                s3_key,
            )
            try:
                await self._storage.delete_files([s3_key])
            except Exception as compensation_err:
                raise FileUploadConsistencyError(
                    details={"s3_key": s3_key},
                ) from compensation_err
            raise

        return db_file

    async def download(self, file_id: int) -> tuple[File, BytesIO]:
        """Fetch DB metadata and download bytes from S3.

        Args:
            file_id: Primary key of the :class:`~apps.files.models.file.File` record.

        Returns:
            Tuple of the DB record and file bytes as :class:`~io.BytesIO`.

        Raises:
            FileMissingError: If no DB record exists for *file_id*.
            shared.database.s3.storage.FileNotExistError: If the S3 object is missing.
        """
        db_file = await self._repo.get_by_id(file_id)
        if db_file is None:
            raise FileMissingError(details={"file_id": file_id})

        data = await self._storage.download_file(db_file.file)
        return db_file, data

    async def delete(self, file_id: int) -> None:
        """Delete DB record first, then remove the S3 object.

        DB is committed before touching S3. If S3 delete fails, the error is
        logged — the object becomes an S3 orphan but will not be served by the app.

        Args:
            file_id: Primary key of the :class:`~apps.files.models.file.File` record.

        Raises:
            FileMissingError: If no DB record exists for *file_id*.
        """
        db_file = await self._repo.get_by_id(file_id)
        if db_file is None:
            raise FileMissingError(details={"file_id": file_id})

        s3_key = db_file.file
        await self._repo.delete_by_id(file_id)
        await self._session.commit()

        try:
            await self._storage.delete_files([s3_key])
        except Exception:
            logger.exception(
                "S3 delete failed for s3_key=%s (file_id=%s); manual cleanup needed",
                s3_key,
                file_id,
            )

    async def get_by_id(self, file_id: int) -> File:
        """Return DB record or raise :exc:`FileMissingError`.

        Args:
            file_id: Primary key of the :class:`~apps.files.models.file.File` record.

        Returns:
            Matching :class:`~apps.files.models.file.File` ORM instance.

        Raises:
            FileMissingError: If no DB record exists for *file_id*.
        """
        db_file = await self._repo.get_by_id(file_id)
        if db_file is None:
            raise FileMissingError(details={"file_id": file_id})
        return db_file

    async def get_download_info(
        self,
        file_id: int,
        *,
        expires_in: int = 3600,
    ) -> FileDownloadDTO:
        """Return a presigned S3 URL for *file_id* plus metadata.

        The URL is signed against the storage's endpoint (MinIO domain), so
        the frontend can hit S3 directly without proxying through this service.
        """
        db_file = await self.get_by_id(file_id)
        url = await self._storage.generate_presigned_url(
            db_file.file,
            expires_in=expires_in,
        )
        expires_at = datetime.datetime.now(tz=datetime.UTC) + datetime.timedelta(
            seconds=expires_in,
        )
        return FileDownloadDTO(
            id=db_file.id,
            key=db_file.file,
            bucket=self._storage.bucket_name,
            download_url=url,
            expires_at=expires_at,
        )
