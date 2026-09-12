"""Добытчик динамограмм ABAI GDIS по скважинам ремонтов-кандидатов.

    python -m apps.repairs.tasks.fetch_sources.fetch_dynamograms
    python -m apps.repairs.tasks.fetch_sources.fetch_dynamograms --repair-id 61818

Celery: ``repairs.fetch.dynamograms`` каждые 15 минут. Тот же загрузчик, что
и полный прогон по фонду (``apps.wells.tasks.load_dynamograms``), но только по
скважинам с открытыми ремонтами: динамограмма «после» появляется в ABAI через
дни после конца ремонта, поэтому скважину надо перечитывать до финализации.
Выбор «до/после» делает аналитика по таблице ``repairs_dynamogram``.
"""

import argparse
import asyncio
from collections import defaultdict

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.repairs.tasks.fetch_sources.candidates import (
    grace_cutoff,
    list_candidate_repairs,
    local_now,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.triggers import (
    DYNAMOGRAMS_FETCH_TASK,
    schedule_repair_analytics,
)
from apps.wells.repositories.well import WellRepository
from apps.wells.tasks.load_dynamograms.load_dynamograms import (
    DEFAULT_CONCURRENCY,
    LoadDynamograms,
)
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


class FetchRepairDynamograms:
    def __init__(
        self,
        *,
        repair_id: int | None = None,
        well_id: int | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
    ) -> None:
        self._repair_id = repair_id
        self._well_id = well_id
        self._concurrency = concurrency

    async def run(self) -> dict[int, int]:
        """Догрузить динамограммы; вернуть число новых файлов по скважинам."""
        now = local_now()
        logger.info(
            "Repair dynamograms fetch started (scope=%s).",
            scope_label(repair_id=self._repair_id, well_id=self._well_id),
        )
        repairs_by_well: dict[int, list[int]] = defaultdict(list)
        async with session_makers["app"]() as session:
            well_repo = WellRepository(session)
            repairs = await list_candidate_repairs(
                session,
                grace_cutoff=grace_cutoff(now),
                repair_id=self._repair_id,
                well_id=self._well_id,
            )
            for repair in repairs:
                well = await resolve_repair_well(repair, well_repo)
                if well is None:
                    logger.warning(
                        "Repair id=%s has no local well; dynamograms skipped.",
                        repair.id,
                    )
                    continue
                repairs_by_well[well.id].append(repair.id)

        if not repairs_by_well:
            logger.info("Repair dynamograms fetch: no wells to load.")
            return {}

        created_by_well = await LoadDynamograms(
            well_ids=sorted(repairs_by_well),
            concurrency=self._concurrency,
        ).run()

        scheduled = 0
        for well_id, created in created_by_well.items():
            if not created:
                continue
            for repair_id in repairs_by_well[well_id]:
                if await schedule_repair_analytics(repair_id):
                    scheduled += 1
        logger.info(
            "Repair dynamograms fetch done: wells=%s, new_files=%s, "
            "analytics_scheduled=%s.",
            len(created_by_well),
            sum(created_by_well.values()),
            scheduled,
        )
        return created_by_well


@celery_app.task(name=DYNAMOGRAMS_FETCH_TASK)
def fetch_repair_dynamograms() -> None:
    run_async(FetchRepairDynamograms().run())


async def main(*, repair_id: int | None = None, well_id: int | None = None) -> None:
    await FetchRepairDynamograms(repair_id=repair_id, well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Fetch ABAI dynamograms for wells of candidate repairs.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repair-id", type=int)
    scope.add_argument("--well-id", type=int)
    args = parser.parse_args()
    asyncio.run(main(repair_id=args.repair_id, well_id=args.well_id))
