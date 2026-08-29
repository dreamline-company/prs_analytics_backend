"""Загрузка всех динамограмм из ABAI GDIS: весь фонд или одна скважина.

    python -m apps.wells.tasks.load_dynamograms.load_dynamograms
    python -m apps.wells.tasks.load_dynamograms.load_dynamograms --well-id 7370

Файлы уезжают в S3 (бакет ремонтов, ключи ``dynamograms/...``), строки — в
``files_file`` и ``repairs_dynamogram``. Идемпотентно: уже сохранённые
(well_id, snapshot_time) пропускаются, повторный запуск докачивает только
новое. measure_date в ABAI — дата без времени, поэтому из нескольких файлов
за один день сохраняется только первый.
"""

import argparse
import asyncio

from sqlalchemy import select

from apps.files.repositories.file import FileRepository
from apps.models_registry import *  # noqa: F403
from apps.repairs.tasks.fill_analytics.fetchers.abai_dynamogram_fetcher import (
    AbaiDynamogramFetcher,
)
from apps.wells.models.well import Well
from apps.wells.repositories.dynamogram import DynamogramRepository
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.dependencies.db import get_aioboto_client_factory
from shared.integrations.abai.api.client import AbaiAsyncClient

logger = get_logger(__name__)
settings = get_settings()


class LoadDynamograms:
    def __init__(self, *, well_id: int | None = None) -> None:
        self._well_id = well_id

    async def run(self) -> None:
        abai_client = AbaiAsyncClient(
            username=settings.ABAI_LOGIN,
            password=settings.ABAI_PASS,
            domain=settings.ABAI_DOMAIN,
            connect_to=settings.ABAI_CONNECT_THROUGH,
            timeout=120,
            max_concurrent_downloads=100,
        )
        storage = AiobotoFileStorage(
            bucket_name=settings.PRS_REPAIRS_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
        )
        try:
            async with session_makers["app"]() as session:
                fetcher = AbaiDynamogramFetcher(
                    abai_client=abai_client,
                    storage=storage,
                    file_repo=FileRepository(session),
                    dynamogram_repo=DynamogramRepository(session),
                )
                wells = await self._target_wells(session)
                logger.info("Dynamogram load: %s wells to process", len(wells))

                total_created = total_skipped = total_failed = 0
                for index, well in enumerate(wells, 1):
                    created, skipped, failed = await fetcher.fetch_all_for_well(
                        well_id=well.id,
                        abai_well_id=well.abai_id,
                    )
                    # Коммит по-скважинно: упавшая скважина не тянет за собой
                    # уже скачанные файлы остальных.
                    await session.commit()
                    total_created += created
                    total_skipped += skipped
                    total_failed += failed
                    logger.info(
                        "[%s/%s] well id=%s (%s): +%s, skipped=%s, failed=%s",
                        index,
                        len(wells),
                        well.id,
                        well.name,
                        created,
                        skipped,
                        failed,
                    )

                logger.info(
                    "Dynamogram load done: created=%s, skipped=%s, failed=%s",
                    total_created,
                    total_skipped,
                    total_failed,
                )
        finally:
            await abai_client.aclose()

    async def _target_wells(self, session) -> list[Well]:  # noqa: ANN001
        stmt = (
            select(Well)
            .where(Well.is_deleted.is_(False), Well.abai_id.is_not(None))
            .order_by(Well.id)
        )
        if self._well_id is not None:
            stmt = stmt.where(Well.id == self._well_id)
        wells = list((await session.execute(stmt)).scalars())
        if self._well_id is not None and not wells:
            msg = f"Well id={self._well_id} not found (or deleted / without abai_id)"
            raise RuntimeError(msg)
        return wells


async def main(*, well_id: int | None = None) -> None:
    await LoadDynamograms(well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load dynamograms from ABAI GDIS; all wells by default.",
    )
    parser.add_argument(
        "--well-id",
        type=int,
        help="только эта скважина (локальный wells_well.id)",
    )
    args = parser.parse_args()
    asyncio.run(main(well_id=args.well_id))
