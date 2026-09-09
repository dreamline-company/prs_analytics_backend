"""Связывание замеров опросчика КБРС (``kbrs_measure``) с ремонтами.

    python -m apps.repairs.tasks.fetch_sources.link_spo
    python -m apps.repairs.tasks.fetch_sources.link_spo --measure-id 124169
    python -m apps.repairs.tasks.fetch_sources.link_spo --repair-id 61818

Celery: ``repairs.link_spo`` — по событию опросчика (``measure_id`` замера,
который появился или вырос) и кроном каждые 30 минут по кандидатам как
страховка. Ни одного RPC в Toucan: замер уже лежит в бакете опросчика,
отсюда он разбирается на события, дорисовывается ``chart.csv`` и уезжает в
бакет ремонтов строкой ``repairs_spo``. Сопоставление — по НГДУ (owner_id
Toucan), номеру скважины из паспорта и пересечению с окном ремонта.
"""

import argparse
import asyncio
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.files.repositories.file import FileRepository
from apps.kbrs.models.measure import MEASURE_STATUS_OK, KbrsMeasure
from apps.kbrs.repositories.measure import KbrsMeasureRepository
from apps.models_registry import *  # noqa: F403
from apps.org.repositories.org import OrgRepository
from apps.org.use_cases.get_ngdu_for_well import GetNGDUForWellUseCase
from apps.repairs.models.repair import Repair
from apps.repairs.tasks.fetch_sources.candidates import (
    RunStats,
    as_naive,
    grace_cutoff,
    list_candidate_repairs,
    local_now,
    repair_window,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.clients import (
    build_kbrs_storage,
    build_repairs_storage,
)
from apps.repairs.tasks.fetch_sources.triggers import (
    SPO_LINK_TASK,
    schedule_repair_analytics,
)
from apps.repairs.tasks.fill_analytics.fetchers.kbrs_spo_fetcher import (
    extract_well_number,
    owner_id_by_ngdu_abai_id,
)
from apps.repairs.tasks.fill_analytics.fetchers.spo_persist import (
    SpoMeasurementPersister,
    is_up_to_date,
)
from apps.wells.models.spo import SPO
from apps.wells.models.well import Well
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_org import WellOrgRepository
from core import get_logger
from shared.constants.kbrs import OWNERS_MAP
from shared.database.s3.storage import AiobotoFileStorage, FileNotExistError
from shared.database.sql.setup import session_makers
from shared.integrations.kbrs.api.exceptions import ToucanDecodeError
from shared.integrations.kbrs.api.parsers import MeasurementFullParser

logger = get_logger(__name__)


def measure_overlaps(
    *,
    measure_start: datetime | None,
    measure_end: datetime | None,
    window: tuple[datetime, datetime],
) -> bool:
    """Замер пересекает окно ремонта [start, end]; без начала — не сопоставим."""
    if measure_start is None:
        return False
    start, end = window
    measure_start = as_naive(measure_start)
    last = as_naive(measure_end) if measure_end is not None else measure_start
    return measure_start <= end and last >= start


def ngdu_abai_id_by_owner(owner_id: int) -> int | None:
    meta = OWNERS_MAP.get(owner_id)
    return int(meta["owner_abai_id"]) if meta else None


class _Context:
    """Репозитории и сервисы одной сессии, чтобы не таскать их по аргументам."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        repairs_storage: AiobotoFileStorage,
        kbrs_storage: AiobotoFileStorage,
    ) -> None:
        self.session = session
        self.well_repo = WellRepository(session)
        self.spo_repo = SPORepository(session)
        self.file_repo = FileRepository(session)
        self.measure_repo = KbrsMeasureRepository(session)
        self.get_ngdu = GetNGDUForWellUseCase(
            well_org_repository=WellOrgRepository(session),
            org_repository=OrgRepository(session),
        )
        self.kbrs_storage = kbrs_storage
        self.persister = SpoMeasurementPersister(
            storage=repairs_storage,
            file_repo=self.file_repo,
            spo_repo=self.spo_repo,
            spo_event_repo=SPOEventRepository(session),
        )


class LinkRepairSpo:
    def __init__(
        self,
        *,
        measure_id: int | None = None,
        repair_id: int | None = None,
        well_id: int | None = None,
    ) -> None:
        self._measure_id = measure_id
        self._repair_id = repair_id
        self._well_id = well_id

    async def run(self) -> RunStats:
        stats = RunStats()
        now = local_now()
        scope = (
            f"measure_id={self._measure_id}"
            if self._measure_id is not None
            else scope_label(repair_id=self._repair_id, well_id=self._well_id)
        )
        logger.info("SPO link started (scope=%s).", scope)
        async with session_makers["app"]() as session:
            ctx = _Context(
                session,
                repairs_storage=build_repairs_storage(),
                kbrs_storage=build_kbrs_storage(),
            )
            repairs = await self._target_repairs(ctx, now=now)
            for repair in repairs:
                try:
                    changed = await self._link_repair(ctx, repair, now=now)
                    await session.commit()
                except Exception:
                    logger.exception(
                        "SPO link failed for repair id=%s; rolled back.",
                        repair.id,
                    )
                    await session.rollback()
                    stats.failed += 1
                    continue
                stats.processed += 1
                if changed:
                    stats.changed += 1
                    await schedule_repair_analytics(repair.id)
        logger.info(
            "SPO link done: processed=%s changed=%s failed=%s.",
            stats.processed,
            stats.changed,
            stats.failed,
        )
        return stats

    async def _target_repairs(self, ctx: _Context, *, now: datetime) -> list[Repair]:
        candidates = await list_candidate_repairs(
            ctx.session,
            grace_cutoff=grace_cutoff(now),
            repair_id=self._repair_id,
            well_id=self._well_id,
        )
        if self._measure_id is None:
            return candidates
        measure = await ctx.measure_repo.get_by_measure_id(self._measure_id)
        if measure is None or measure.status != MEASURE_STATUS_OK:
            logger.warning("Measure %s not found or not parsed.", self._measure_id)
            return []
        if measure.well_number is None:
            logger.info("Measure %s has no well in passport.", self._measure_id)
            return []
        return await self._repairs_for_measure(ctx, measure, candidates, now=now)

    @staticmethod
    async def _repairs_for_measure(
        ctx: _Context,
        measure: KbrsMeasure,
        candidates: Sequence[Repair],
        *,
        now: datetime,
    ) -> list[Repair]:
        """Кандидаты той же скважины (номер + НГДУ), окно которых задевает замер."""
        ngdu_abai_id = ngdu_abai_id_by_owner(measure.owner_id)
        if ngdu_abai_id is None:
            return []
        matched: list[Repair] = []
        for repair in candidates:
            well = await resolve_repair_well(repair, ctx.well_repo)
            if well is None or extract_well_number(well.name) != measure.well_number:
                continue
            if not measure_overlaps(
                measure_start=measure.start_time,
                measure_end=measure.end_time,
                window=repair_window(repair, now=now),
            ):
                continue
            ngdu = await ctx.get_ngdu.execute(well.abai_id)
            if ngdu is not None and ngdu.abai_id == ngdu_abai_id:
                matched.append(repair)
        return matched

    async def _link_repair(
        self,
        ctx: _Context,
        repair: Repair,
        *,
        now: datetime,
    ) -> bool:
        well = await resolve_repair_well(repair, ctx.well_repo)
        if well is None:
            logger.warning("Repair id=%s has no local well; SPO skipped.", repair.id)
            return False
        owner_id = await self._owner_id(ctx, well)
        well_number = extract_well_number(well.name)
        if owner_id is None or well_number is None:
            logger.warning(
                "Repair id=%s: cannot map well %r to Toucan (owner=%s, number=%s).",
                repair.id,
                well.name,
                owner_id,
                well_number,
            )
            return False

        window = repair_window(repair, now=now)
        measures = await ctx.measure_repo.list_matching(
            owner_id=owner_id,
            well_number=well_number,
            start=window[0],
            end=window[1],
        )
        changed = False
        for measure in measures:
            existing = await ctx.spo_repo.get_by_kbrs_measure_id(
                measure.measure_id,
                well_id=well.id,
            )
            if is_up_to_date(existing, raw_size=measure.raw_size):
                continue
            if await self._link_measure(ctx, repair, well, measure, existing):
                changed = True
        logger.info(
            "Repair id=%s: %s matching measures, changed=%s.",
            repair.id,
            len(measures),
            changed,
        )
        return changed

    @staticmethod
    async def _owner_id(ctx: _Context, well: Well) -> int | None:
        ngdu = await ctx.get_ngdu.execute(well.abai_id)
        return owner_id_by_ngdu_abai_id(ngdu.abai_id) if ngdu is not None else None

    @staticmethod
    async def _link_measure(
        ctx: _Context,
        repair: Repair,
        well: Well,
        measure: KbrsMeasure,
        existing: SPO | None,
    ) -> bool:
        raw = await _download_raw(ctx, measure)
        if raw is None:
            return False
        try:
            full = await asyncio.to_thread(MeasurementFullParser.parse, raw)
        except ToucanDecodeError as exc:
            logger.warning(
                "Measure %s: parse failed (%s); repair id=%s skipped.",
                measure.measure_id,
                exc,
                repair.id,
            )
            return False
        spo = await ctx.persister.persist(
            well_id=well.id,
            measure_id=measure.measure_id,
            raw_bytes=raw,
            full=full,
            fallback_snapshot_time=measure.start_time or repair.start_time,
            existing=existing,
        )
        if spo is None:
            return False
        logger.info(
            "Repair id=%s: measure %s linked as spo_id=%s (%s bytes).",
            repair.id,
            measure.measure_id,
            spo.id,
            len(raw),
        )
        return True


async def _download_raw(ctx: _Context, measure: KbrsMeasure) -> bytes | None:
    if measure.raw_file_id is None:
        return None
    file_row = await ctx.file_repo.get_by_id(measure.raw_file_id)
    if file_row is None:
        return None
    try:
        buf = await ctx.kbrs_storage.download_file(file_row.file)
    except FileNotExistError:
        logger.warning("Raw payload missing in S3 for measure %s.", measure.measure_id)
        return None
    return buf.getvalue()


@celery_app.task(name=SPO_LINK_TASK)
def link_repair_spo(measure_id: int | None = None) -> None:
    run_async(LinkRepairSpo(measure_id=measure_id).run())


async def main(
    *,
    measure_id: int | None = None,
    repair_id: int | None = None,
    well_id: int | None = None,
) -> None:
    await LinkRepairSpo(
        measure_id=measure_id,
        repair_id=repair_id,
        well_id=well_id,
    ).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Link KBRS measures to repairs.")
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument("--measure-id", type=int)
    scope.add_argument("--repair-id", type=int)
    scope.add_argument("--well-id", type=int)
    args = parser.parse_args()
    asyncio.run(
        main(
            measure_id=args.measure_id,
            repair_id=args.repair_id,
            well_id=args.well_id,
        ),
    )
