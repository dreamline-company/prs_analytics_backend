"""Точка входа постоянного опросчика замеров КБРС/Toucan.

Запуск:
    cd src && python -m apps.kbrs.tasks.poll_measures.poll_measures

Поднимает пул залогиненных Toucan-сессий и бесконечно опрашивает замеры
(см. ``poller.KbrsMeasurePoller``). Останавливается по SIGINT/SIGTERM:
дорабатывает текущую операцию, закрывает пул и выходит.
"""

import asyncio
import signal

from apps.kbrs.tasks.poll_measures.poller import KbrsMeasurePoller
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import get_aioboto_client_factory
from shared.integrations.kbrs.api import (
    ToucanClientConfig,
    ToucanClientPool,
    ToucanCredentialsDto,
)

logger = get_logger(__name__)


async def main() -> None:
    settings = get_settings()
    pool = await ToucanClientPool.create(
        size=settings.KBRS_POOL_SIZE,
        config=ToucanClientConfig(host=settings.KBRS_HOST),
        credentials=ToucanCredentialsDto(
            login=settings.KBRS_LOGIN,
            password=settings.KBRS_PASSWORD,
        ),
    )
    storage = AiobotoFileStorage(
        bucket_name=settings.S3_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
    )
    poller = KbrsMeasurePoller(
        pool=pool,
        storage=storage,
        poll_interval_seconds=settings.KBRS_POLL_INTERVAL_SECONDS,
        keepalive_interval_seconds=settings.KBRS_KEEPALIVE_INTERVAL_SECONDS,
        window_hours=settings.KBRS_POLL_WINDOW_HOURS,
        refresh_grace_minutes=settings.KBRS_POLL_REFRESH_GRACE_MINUTES,
        page_count=settings.KBRS_POLL_PAGE_COUNT,
    )

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, poller.request_stop)

    try:
        await poller.run()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
