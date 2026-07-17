from collections.abc import AsyncGenerator, Callable
from typing import AsyncContextManager, cast

import aioboto3
from botocore.client import Config
from sqlalchemy.ext.asyncio import AsyncSession

from core.settings import get_settings
from shared.database.s3.interface import AiobotoClient
from shared.database.sql.setup import AVAILABLE_DB, session_makers

settings = get_settings()


async def get_session(db_name: AVAILABLE_DB) -> AsyncGenerator[AsyncSession]:
    async with session_makers[db_name]() as session:
        yield session


async def get_app_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_makers["app"]() as session:
        yield session


async def get_cm_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_makers["cm"]() as session:
        yield session


async def get_abai_session() -> AsyncGenerator[AsyncSession, None]:
    async with session_makers["abai"]() as session:
        yield session


def get_aioboto_client_factory(
    *,
    endpoint_url: str | None = None,
) -> Callable[[], AsyncContextManager[AiobotoClient]]:
    endpoint = endpoint_url or settings.S3_ENDPOINT_URL

    def factory() -> "AsyncContextManager[AiobotoClient]":
        session = aioboto3.Session()
        return cast(
            "AsyncContextManager[AiobotoClient]",
            session.client(
                "s3",
                aws_access_key_id=settings.S3_ACCESS_KEY,
                aws_secret_access_key=settings.S3_SECRET_KEY,
                endpoint_url=endpoint,
                config=Config(
                    signature_version="s3v4",
                    request_checksum_calculation="when_required",
                    response_checksum_validation="when_required",
                ),
            ),
        )

    return factory


def get_aioboto_presign_client_factory() -> (
    Callable[[], AsyncContextManager[AiobotoClient]]
):
    # Presigned URLs are frontend-facing. Signature is bound to the endpoint
    # host, so we build a dedicated client against ``S3_PUBLIC_URL`` when it's
    # configured — otherwise falls back to the internal endpoint so nothing
    # breaks in single-host setups.
    return get_aioboto_client_factory(
        endpoint_url=settings.S3_PUBLIC_URL,
    )
