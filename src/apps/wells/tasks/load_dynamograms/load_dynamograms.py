"""Загрузка всех динамограмм из ABAI GDIS: весь фонд или одна скважина.

    python -m apps.wells.tasks.load_dynamograms.load_dynamograms
    python -m apps.wells.tasks.load_dynamograms.load_dynamograms --well-id 7370
    python -m apps.wells.tasks.load_dynamograms.load_dynamograms --concurrency 12

Скважины обрабатываются параллельно под семафором: у каждой — своя сессия БД
(общая AsyncSession конкурентности не переживает) и свой коммит, ABAI-клиент
и S3-хранилище общие. Файлы уезжают в S3 (бакет ремонтов, ключи
``dynamograms/...``), строки — в ``files_file`` и ``repairs_dynamogram``.

Идемпотентно: уже сохранённые (well_id, snapshot_time) пропускаются, повторный
запуск докачивает только новое. measure_date в ABAI — дата без времени, поэтому
из нескольких файлов за один день сохраняется только первый.
"""

import argparse
import asyncio
from collections.abc import Sequence

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

DEFAULT_CONCURRENCY = 8
# Сессия держит соединение с БД на всё время обработки скважины (включая
# скачивания), а пул app-движка — pool_size 5 + max_overflow 10. Выше этой
# планки задачи начнут отваливаться по таймауту чекаута, а не ускоряться.
MAX_CONCURRENCY = 15


class LoadDynamograms:
    def __init__(
        self,
        *,
        well_id: int | None = None,
        well_ids: Sequence[int] | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._well_id = well_id
        # Явный список скважин — для добытчика по ремонтам-кандидатам: он
        # перечитывает только скважины с открытыми ремонтами, а не весь фонд.
        self._well_ids = list(well_ids) if well_ids is not None else None
        if concurrency > MAX_CONCURRENCY:
            logger.warning(
                "Concurrency %s exceeds DB pool capacity, capped to %s",
                concurrency,
                MAX_CONCURRENCY,
            )
            concurrency = MAX_CONCURRENCY
        self._concurrency = max(concurrency, 1)

    async def run(self) -> dict[int, int]:
        """Загрузить динамограммы; вернуть число новых файлов по скважинам."""
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
                wells = await self._target_wells(session)
            logger.info(
                "Dynamogram load: %s wells, concurrency=%s",
                len(wells),
                self._concurrency,
            )

            semaphore = asyncio.Semaphore(self._concurrency)
            done_counter = [0]
            results = await asyncio.gather(
                *(
                    self._process_well(
                        well,
                        abai_client=abai_client,
                        storage=storage,
                        semaphore=semaphore,
                        done_counter=done_counter,
                        total=len(wells),
                    )
                    for well in wells
                ),
            )

            created = sum(r[0] for r in results)
            skipped = sum(r[1] for r in results)
            failed = sum(r[2] for r in results)
            no_gdis = sum(r[3] for r in results)
            failed_wells = sum(r[4] for r in results)
            logger.info(
                "Dynamogram load done: created=%s, skipped=%s, "
                "failed_files=%s, wells_without_gdis=%s, failed_wells=%s",
                created,
                skipped,
                failed,
                no_gdis,
                failed_wells,
            )
            return {
                well.id: result[0] for well, result in zip(wells, results, strict=True)
            }
        finally:
            await abai_client.aclose()

    async def _process_well(  # noqa: PLR0913
        self,
        well: Well,
        *,
        abai_client: AbaiAsyncClient,
        storage: AiobotoFileStorage,
        semaphore: asyncio.Semaphore,
        done_counter: list[int],
        total: int,
    ) -> tuple[int, int, int, int, int]:
        """Одна скважина: своя сессия, свой коммит. Возвращает счётчики.

        (created, skipped, failed_files, no_gdis, failed_wells) — скважина без
        ГДИС-формы (ABAI отвечает 400) идёт в no_gdis, упавшая — в failed_wells;
        ни та ни другая прогон не валят.
        """
        async with semaphore:
            try:
                async with session_makers["app"]() as session:
                    fetcher = AbaiDynamogramFetcher(
                        abai_client=abai_client,
                        storage=storage,
                        file_repo=FileRepository(session),
                        dynamogram_repo=DynamogramRepository(session),
                    )
                    (
                        created,
                        skipped,
                        failed,
                        listing_failed,
                    ) = await fetcher.fetch_all_for_well(
                        well_id=well.id,
                        abai_well_id=well.abai_id,
                    )
                    await session.commit()
            except Exception:
                logger.exception("Well id=%s (%s) failed", well.id, well.name)
                return (0, 0, 0, 0, 1)

        done_counter[0] += 1
        if listing_failed:
            logger.info(
                "[%s/%s] well id=%s (%s): ГДИС недоступен, пропущена",
                done_counter[0],
                total,
                well.id,
                well.name,
            )
            return (0, 0, 0, 1, 0)

        logger.info(
            "[%s/%s] well id=%s (%s): +%s, skipped=%s, failed=%s",
            done_counter[0],
            total,
            well.id,
            well.name,
            created,
            skipped,
            failed,
        )
        return (created, skipped, failed, 0, 0)

    async def _target_wells(self, session) -> list[Well]:  # noqa: ANN001
        stmt = (
            select(Well)
            .where(Well.is_deleted.is_(False), Well.abai_id.is_not(None))
            .order_by(Well.id)
        )
        if self._well_id is not None:
            stmt = stmt.where(Well.id == self._well_id)
        if self._well_ids is not None:
            if not self._well_ids:
                return []
            stmt = stmt.where(Well.id.in_(self._well_ids))
        wells = list((await session.execute(stmt)).scalars())
        if self._well_id is not None and not wells:
            msg = f"Well id={self._well_id} not found (or deleted / without abai_id)"
            raise RuntimeError(msg)
        return wells


async def main(
    *,
    well_id: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    await LoadDynamograms(well_id=well_id, concurrency=concurrency).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Load dynamograms from ABAI GDIS; all wells by default.",
    )
    parser.add_argument(
        "--well-id",
        type=int,
        help="только эта скважина (локальный wells_well.id)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"скважин параллельно (default {DEFAULT_CONCURRENCY}, "
        f"max {MAX_CONCURRENCY} — упирается в пул соединений БД)",
    )
    args = parser.parse_args()
    asyncio.run(main(well_id=args.well_id, concurrency=args.concurrency))
