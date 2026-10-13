"""Разовая перекачка недавних замеров КБРС — подтянуть досланные хвосты архива.

    cd src && python -m apps.kbrs.tasks.poll_measures.refresh_recent --days 7

Тот же проход, что опросчик делает раз в ``KBRS_LATE_SWEEP_MINUTES``
(``KbrsMeasurePoller.late_sweep``), но за произвольное окно и без паузы после
прошлого скачивания: каждый разобранный замер, начатый за ``--days`` суток,
скачивается заново; выросший разбирается и ставится на перепривязку к ремонту.
Нужен один раз — для замеров, оборванных до появления фонового прохода.
"""

import argparse
import asyncio
from datetime import timedelta

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


async def main(days: int) -> int:
    settings = get_settings()
    pool = await ToucanClientPool.create(
        size=2,
        config=ToucanClientConfig(host=settings.KBRS_HOST),
        credentials=ToucanCredentialsDto(
            login=settings.KBRS_LOGIN,
            password=settings.KBRS_PASSWORD,
        ),
    )
    poller = KbrsMeasurePoller(
        pool=pool,
        storage=AiobotoFileStorage(
            bucket_name=settings.S3_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
        ),
        poll_interval_seconds=settings.KBRS_POLL_INTERVAL_SECONDS,
        keepalive_interval_seconds=settings.KBRS_KEEPALIVE_INTERVAL_SECONDS,
        window_hours=settings.KBRS_POLL_WINDOW_HOURS,
        refresh_grace_minutes=settings.KBRS_POLL_REFRESH_GRACE_MINUTES,
        page_count=settings.KBRS_POLL_PAGE_COUNT,
        late_sweep_minutes=settings.KBRS_LATE_SWEEP_MINUTES,
        late_sweep_window_hours=settings.KBRS_LATE_SWEEP_WINDOW_HOURS,
    )
    try:
        return await poller.late_sweep(
            window=timedelta(days=days),
            min_age=timedelta(0),
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Перекачка недавних замеров КБРС")
    parser.add_argument("--days", type=int, default=7, help="Окно по началу замера")
    args = parser.parse_args()
    asyncio.run(main(args.days))
