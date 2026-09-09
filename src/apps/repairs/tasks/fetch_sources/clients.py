"""Фабрики клиентов внешних источников для добытчиков ремонтов.

Каждый добытчик поднимает только своего клиента: падение или медленный ответ
одного источника не задерживает остальные и не касается аналитики.
"""

import asyncio

from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import get_aioboto_client_factory
from shared.integrations.abai.api.client import AbaiAsyncClient
from shared.integrations.kbrs.api import (
    ToucanClientConfig,
    ToucanClientPool,
    ToucanCredentialsDto,
)
from shared.integrations.uto.api.client import UtoWaybillClient

settings = get_settings()


def build_abai_client() -> AbaiAsyncClient:
    return AbaiAsyncClient(
        username=settings.ABAI_LOGIN,
        password=settings.ABAI_PASS,
        domain=settings.ABAI_DOMAIN,
        connect_to=settings.ABAI_CONNECT_THROUGH,
        timeout=120,
        max_concurrent_downloads=100,
    )


def build_repairs_storage() -> AiobotoFileStorage:
    """Бакет ремонтов: документы, динамограммы, СПО — всё, что читают API и AI."""
    return AiobotoFileStorage(
        bucket_name=settings.PRS_REPAIRS_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
    )


def build_kbrs_storage() -> AiobotoFileStorage:
    """Бакет опросчика КБРС: сырые замеры лежат отдельно от бакета ремонтов."""
    return AiobotoFileStorage(
        bucket_name=settings.SPO_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
    )


async def build_uto_client() -> UtoWaybillClient:
    client = UtoWaybillClient(
        login=settings.UTO_LOGIN,
        password=settings.UTO_PASS,
        connect_to=settings.UTO_CONNECT_THROUGH,
        verify=False,
    )
    await asyncio.to_thread(client.login)
    return client


async def close_uto_client(client: UtoWaybillClient) -> None:
    await asyncio.to_thread(client.close)


async def create_toucan_pool() -> ToucanClientPool:
    return await ToucanClientPool.create(
        size=settings.KBRS_POOL_SIZE,
        config=ToucanClientConfig(host=settings.KBRS_HOST),
        credentials=ToucanCredentialsDto(
            login=settings.KBRS_LOGIN,
            password=settings.KBRS_PASSWORD,
        ),
    )
