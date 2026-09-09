"""Добытчик ПОР/актов из ABAI для ремонтов-кандидатов.

    python -m apps.repairs.tasks.fetch_sources.fetch_docs
    python -m apps.repairs.tasks.fetch_sources.fetch_docs --repair-id 61818
    python -m apps.repairs.tasks.fetch_sources.fetch_docs --well-id 36

Celery: ``repairs.fetch.docs`` каждые 15 минут. На ремонт два запроса в ABAI,
PDF сравнивается по хэшу и перезаливается только при изменении. Изменившийся
документ ставит аналитику ремонта в очередь (с debounce).
"""

import argparse
import asyncio

from apps.celery_app import celery_app, run_async
from apps.files.repositories.file import FileRepository
from apps.models_registry import *  # noqa: F403
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.tasks.fetch_sources.candidates import (
    RunStats,
    grace_cutoff,
    iter_candidate_repairs,
    local_now,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.clients import (
    build_abai_client,
    build_repairs_storage,
)
from apps.repairs.tasks.fetch_sources.triggers import (
    DOCS_FETCH_TASK,
    schedule_repair_analytics,
)
from apps.repairs.tasks.fill_analytics.fetchers.abai_repair_doc_fetcher import (
    AbaiRepairDocFetcher,
)
from apps.wells.repositories.well import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.api.client import AbaiAsyncClient

logger = get_logger(__name__)


class FetchRepairDocs:
    def __init__(
        self,
        *,
        repair_id: int | None = None,
        well_id: int | None = None,
        abai_client: AbaiAsyncClient | None = None,
    ) -> None:
        self._repair_id = repair_id
        self._well_id = well_id
        self._abai_client = abai_client

    async def run(self) -> RunStats:
        stats = RunStats()
        now = local_now()
        logger.info(
            "Repair docs fetch started (scope=%s).",
            scope_label(repair_id=self._repair_id, well_id=self._well_id),
        )
        abai_client = self._abai_client or build_abai_client()
        storage = build_repairs_storage()
        try:
            async with session_makers["app"]() as session:
                well_repo = WellRepository(session)
                fetcher = AbaiRepairDocFetcher(
                    abai_client=abai_client,
                    storage=storage,
                    file_repo=FileRepository(session),
                    repair_doc_repo=RepairDocRepository(session),
                )
                async for repairs in iter_candidate_repairs(
                    session,
                    grace_cutoff=grace_cutoff(now),
                    repair_id=self._repair_id,
                    well_id=self._well_id,
                ):
                    for repair in repairs:
                        try:
                            changed = await self._fetch_one(repair, fetcher, well_repo)
                            await session.commit()
                        except Exception:
                            logger.exception(
                                "Docs fetch failed for repair id=%s; rolled back.",
                                repair.id,
                            )
                            await session.rollback()
                            stats.failed += 1
                            continue
                        stats.processed += 1
                        if changed:
                            stats.changed += 1
                            await schedule_repair_analytics(repair.id)
        finally:
            if self._abai_client is None:
                await abai_client.aclose()
        logger.info(
            "Repair docs fetch done: processed=%s changed=%s failed=%s.",
            stats.processed,
            stats.changed,
            stats.failed,
        )
        return stats

    @staticmethod
    async def _fetch_one(
        repair: Repair,
        fetcher: AbaiRepairDocFetcher,
        well_repo: WellRepository,
    ) -> bool:
        well = await resolve_repair_well(repair, well_repo)
        abai_well_id = well.abai_id if well is not None else repair.abai_well_id
        if abai_well_id is None:
            logger.warning("Repair id=%s has no abai_well_id; docs skipped.", repair.id)
            return False
        outcome = await fetcher.fetch(repair, abai_well_id)
        logger.info(
            "Repair id=%s doc=%s changed=%s",
            repair.id,
            outcome.doc.id if outcome.doc else None,
            outcome.changed,
        )
        return outcome.changed


@celery_app.task(name=DOCS_FETCH_TASK)
def fetch_repair_docs() -> None:
    run_async(FetchRepairDocs().run())


async def main(*, repair_id: int | None = None, well_id: int | None = None) -> None:
    await FetchRepairDocs(repair_id=repair_id, well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch ПОР/акт PDFs from ABAI.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repair-id", type=int)
    scope.add_argument("--well-id", type=int)
    args = parser.parse_args()
    asyncio.run(main(repair_id=args.repair_id, well_id=args.well_id))
