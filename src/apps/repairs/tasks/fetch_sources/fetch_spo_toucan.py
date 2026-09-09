"""Запасной добытчик СПО напрямую из Toucan — для ремонтов без замеров в
``kbrs_measure``.

    python -m apps.repairs.tasks.fetch_sources.fetch_spo_toucan
    python -m apps.repairs.tasks.fetch_sources.fetch_spo_toucan --repair-id 61818

Celery: ``repairs.fetch.spo_toucan`` раз в 6 часов. Основной путь —
``repairs.link_spo`` по замерам опросчика; сюда попадают ремонты, у которых в
окне нет ни одного СПО: история до запуска опросчика и его простои. Путь
дорогой (десятки RPC на ремонт), поэтому редкий и только по таким ремонтам.
"""

import argparse
import asyncio

from apps.celery_app import celery_app, run_async
from apps.files.repositories.file import FileRepository
from apps.models_registry import *  # noqa: F403
from apps.org.repositories.org import OrgRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.repairs.tasks.fetch_sources.candidates import (
    RunStats,
    grace_cutoff,
    iter_candidate_repairs,
    local_now,
    repair_window,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.clients import (
    build_repairs_storage,
    create_toucan_pool,
)
from apps.repairs.tasks.fetch_sources.triggers import (
    SPO_TOUCAN_FETCH_TASK,
    schedule_repair_analytics,
)
from apps.repairs.tasks.fill_analytics.fetchers.kbrs_spo_fetcher import (
    KbrsSPOFetcher,
)
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

# Toucan отвечает медленно и по многу раз на ремонт: прогону нужен запас
# сверх общего лимита celery.
TIME_LIMIT_SEC = 60 * 60


class FetchRepairSpoToucan:
    def __init__(
        self,
        *,
        repair_id: int | None = None,
        well_id: int | None = None,
    ) -> None:
        self._repair_id = repair_id
        self._well_id = well_id

    async def run(self) -> RunStats:
        stats = RunStats()
        now = local_now()
        logger.info(
            "SPO Toucan fetch started (scope=%s).",
            scope_label(repair_id=self._repair_id, well_id=self._well_id),
        )
        pool = await create_toucan_pool()
        try:
            async with session_makers["app"]() as session:
                well_repo = WellRepository(session)
                spo_repo = SPORepository(session)
                fetcher = KbrsSPOFetcher(
                    pool=pool,
                    storage=build_repairs_storage(),
                    file_repo=FileRepository(session),
                    spo_repo=spo_repo,
                    spo_event_repo=SPOEventRepository(session),
                    get_ngdu_for_well=GetNGDUForWellUseCase(
                        well_org_repository=WellOrgRepository(session),
                        org_repository=OrgRepository(session),
                    ),
                )
                async for repairs in iter_candidate_repairs(
                    session,
                    grace_cutoff=grace_cutoff(now),
                    repair_id=self._repair_id,
                    well_id=self._well_id,
                ):
                    for repair in repairs:
                        try:
                            well = await resolve_repair_well(repair, well_repo)
                            if well is None:
                                continue
                            start, end = repair_window(repair, now=now)
                            if await spo_repo.list_by_well_id_in_window(
                                well.id,
                                start,
                                end,
                            ):
                                # Замеры уже есть (линкер или прошлый прогон) —
                                # Toucan ради них не дёргаем.
                                continue
                            spos = await fetcher.fetch_for_repair(
                                repair,
                                well_id=well.id,
                                well_name=well.name,
                                abai_well_id=well.abai_id,
                            )
                            await session.commit()
                        except Exception:
                            logger.exception(
                                "SPO Toucan failed for repair id=%s; rolled back.",
                                repair.id,
                            )
                            await session.rollback()
                            stats.failed += 1
                            continue
                        stats.processed += 1
                        if spos:
                            stats.changed += 1
                            await schedule_repair_analytics(repair.id)
        finally:
            await pool.close()
        logger.info(
            "SPO Toucan fetch done: processed=%s changed=%s failed=%s.",
            stats.processed,
            stats.changed,
            stats.failed,
        )
        return stats


@celery_app.task(
    name=SPO_TOUCAN_FETCH_TASK,
    time_limit=TIME_LIMIT_SEC,
    soft_time_limit=TIME_LIMIT_SEC - 5 * 60,
)
def fetch_repair_spo_toucan() -> None:
    run_async(FetchRepairSpoToucan().run())


async def main(*, repair_id: int | None = None, well_id: int | None = None) -> None:
    await FetchRepairSpoToucan(repair_id=repair_id, well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch SPO from Toucan for repairs without linked measures.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repair-id", type=int)
    scope.add_argument("--well-id", type=int)
    args = parser.parse_args()
    asyncio.run(main(repair_id=args.repair_id, well_id=args.well_id))
