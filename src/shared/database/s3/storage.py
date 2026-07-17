from collections.abc import Callable, Iterable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

from botocore.exceptions import ClientError

from core import get_logger
from shared.database.s3.interface import AiobotoClient

logger = get_logger(__name__)


class S3Error(Exception): ...


class FileNotExistError(S3Error): ...


class FileUploadError(S3Error): ...


@dataclass(slots=True)
class AiobotoFileStorage:
    bucket_name: str
    client_factory: Callable[[], AbstractAsyncContextManager[AiobotoClient]]
    # Optional separate client used only for ``generate_presigned_url`` so the
    # signed URL points at a public host while server-side ops stay on the
    # internal endpoint. When ``None``, presigning reuses ``client_factory``.
    presign_client_factory: (
        Callable[[], AbstractAsyncContextManager[AiobotoClient]] | None
    ) = None

    async def download_file(self, file_path: str) -> BytesIO:
        file = BytesIO()
        try:
            async with self.client_factory() as client:
                await client.download_fileobj(
                    Bucket=self.bucket_name,
                    Key=file_path,
                    Fileobj=file,
                )
        except ClientError as e:
            raise FileNotExistError from e
        file.seek(0)
        return file

    async def download_file_to_path(self, file_path: str, local_path: Path) -> None:
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            async with self.client_factory() as client:
                await client.download_file(
                    Bucket=self.bucket_name,
                    Key=file_path,
                    Filename=local_path.as_posix(),
                )
        except ClientError as e:
            raise FileNotExistError from e

    async def upload_file(self, file: BytesIO, file_path: str) -> None:
        try:
            async with self.client_factory() as client:
                await client.upload_fileobj(
                    Fileobj=file,
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
        except Exception as e:
            raise FileUploadError from e

    async def upload_file_from_path(self, local_path: Path, file_path: str) -> None:
        try:
            async with self.client_factory() as client:
                await client.upload_file(
                    Filename=local_path.as_posix(),
                    Bucket=self.bucket_name,
                    Key=file_path,
                )
        except Exception as e:
            raise FileUploadError from e

    async def check_file(self, file_path: str) -> bool:
        try:
            async with self.client_factory() as client:
                await client.head_object(Bucket=self.bucket_name, Key=file_path)
                return True
        except ClientError as e:
            if e.response["Error"]["Code"] != "404":
                logger.exception("Unhandled error when s3 check file")
            return False

    async def delete_files(self, files: Iterable[str]) -> None:
        for file in files:
            try:
                async with self.client_factory() as client:
                    await client.delete_object(
                        Bucket=self.bucket_name,
                        Key=file,
                    )
            except ClientError:
                logger.exception("No files for delete in S3 bucket")

    async def generate_presigned_url(
        self,
        file_path: str,
        *,
        expires_in: int = 3600,
    ) -> str:
        """Return a time-limited download URL for the object.

        Works with MinIO — the URL uses the same endpoint (domain) that the
        client is configured with, so this is exactly the domain URL the
        frontend needs.
        """
        factory = self.presign_client_factory or self.client_factory
        async with factory() as client:
            return await client.generate_presigned_url(
                ClientMethod="get_object",
                Params={"Bucket": self.bucket_name, "Key": file_path},
                ExpiresIn=expires_in,
            )

    async def list_dir(self, prefix: str) -> list[str] | None:
        try:
            async with self.client_factory() as client:
                files = (
                    await client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)
                ).get("Contents", [])
                return [obj["Key"] for obj in files if "Key" in obj]
        except ClientError:
            logger.exception("Error while listing S3 bucket")
