"""Добытчик путёвок спецтранспорта из УТО для ремонтов.

    python -m apps.repairs.tasks.fetch_sources.fetch_transport
    python -m apps.repairs.tasks.fetch_sources.fetch_transport --repair-id 61818

Celery: ``repairs.fetch.transport`` — по событию загрузки сводок (явный список
``repair_ids``, без фильтра финализации: сводки грузят и за прошлые месяцы) и
кроном раз в два часа по кандидатам, чтобы подтянуть смену статусов путёвок.
Транспорт в AI и KPI не участвует, аналитику не триггерит.
"""

import argparse
import asyncio
from collections.abc import Sequence

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from apps.repairs.tasks.fetch_sources.candidates import (
    RunStats,
    grace_cutoff,
    iter_candidate_repairs,
    local_now,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.clients import (
    build_uto_client,
    close_uto_client,
)
from apps.repairs.tasks.fetch_sources.triggers import TRANSPORT_FETCH_TASK
from apps.repairs.tasks.fill_analytics.fetchers.uto_transport_fetcher import (
    UtoTransportFetcher,
)
from apps.wells.repositories.well import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.uto.api.client import UtoWaybillClient

logger = get_logger(__name__)


class FetchRepairTransport:
    def __init__(
        self,
        *,
        repair_ids: Sequence[int] | None = None,
        repair_id: int | None = None,
        well_id: int | None = None,
        uto_client: UtoWaybillClient | None = None,
    ) -> None:
        self._repair_ids = list(repair_ids) if repair_ids is not None else None
        self._repair_id = repair_id
        self._well_id = well_id
        self._uto_client = uto_client

    async def run(self) -> RunStats:
        stats = RunStats()
        now = local_now()
        logger.info(
            "Repair transport fetch started (scope=%s).",
            scope_label(
                repair_id=self._repair_id,
                well_id=self._well_id,
                repair_ids=self._repair_ids,
            ),
        )
        uto_client = self._uto_client or await build_uto_client()
        try:
            async with session_makers["app"]() as session:
                well_repo = WellRepository(session)
                fetcher = UtoTransportFetcher(
                    client=uto_client,
                    summary_repo=RepairSummaryRepository(session),
                    transport_repo=RepairTransportRepository(session),
                )
                async for repairs in iter_candidate_repairs(
                    session,
                    grace_cutoff=grace_cutoff(now),
                    repair_id=self._repair_id,
                    well_id=self._well_id,
                    repair_ids=self._repair_ids,
                ):
                    for repair in repairs:
                        try:
                            well = await resolve_repair_well(repair, well_repo)
                            rows = await fetcher.fetch_for_repair(
                                repair,
                                well_id=well.id if well is not None else None,
                            )
                            await session.commit()
                        except Exception:
                            logger.exception(
                                "Transport fetch failed for repair id=%s; rolled back.",
                                repair.id,
                            )
                            await session.rollback()
                            stats.failed += 1
                            continue
                        stats.processed += 1
                        if rows:
                            stats.changed += 1
        finally:
            if self._uto_client is None:
                await close_uto_client(uto_client)
        logger.info(
            "Repair transport fetch done: processed=%s with_waybills=%s failed=%s.",
            stats.processed,
            stats.changed,
            stats.failed,
        )
        return stats


@celery_app.task(name=TRANSPORT_FETCH_TASK)
def fetch_repair_transport(repair_ids: list[int] | None = None) -> None:
    run_async(FetchRepairTransport(repair_ids=repair_ids).run())


async def main(*, repair_id: int | None = None, well_id: int | None = None) -> None:
    await FetchRepairTransport(repair_id=repair_id, well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch UTO waybills for repairs.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--repair-id", type=int)
    scope.add_argument("--well-id", type=int)
    args = parser.parse_args()
    asyncio.run(main(repair_id=args.repair_id, well_id=args.well_id))
