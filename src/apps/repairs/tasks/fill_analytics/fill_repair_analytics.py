"""Periodically fills RepairAnalytics by pulling docs/dynamograms/SPO from APIs.

Sources:
  * dynamograms — ABAI GDIS API (``AbaiAsyncClient.get_gdis_results``)
  * ПОР/Акт PDF — ABAI PRS API (``AbaiAsyncClient.get_prs_results``)
  * SPO        — kbrs/Toucan RPC (``ToucanBackendClient``), routed via the
                 brigade number on ``RepairSummary`` (бригада владеет девайсом).

Selection logic per repair:
  * not finalized AND (active OR within grace window OR analytics missing)
    → process this run.

Finalization (mark ``is_finalized=True``, stop touching) — either:
  * RepairDoc has both ``por_file_id`` and ``act_file_id`` (the act PDF arrived);
  * OR end_time + 10 days have passed (grace period for late docs).
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.repositories.file import FileRepository
from apps.models_registry import *  # noqa: F403
from apps.repairs.dto.internal.repositories.analytics import (
    CreateRepairAnalyticsDTO,
    CreateRepairAnalyticsDynamogramDTO,
    CreateRepairAnalyticsSPODTO,
    UpdateRepairAnalyticsDTO,
    UpdateRepairAnalyticsDynamogramDTO,
    UpdateRepairAnalyticsSPODTO,
)
from apps.repairs.models.analytics import RepairAnalytics
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.tasks.fill_analytics.fetchers.abai_dynamogram_fetcher import (
    AbaiDynamogramFetcher,
)
from apps.repairs.tasks.fill_analytics.fetchers.abai_repair_doc_fetcher import (
    AbaiRepairDocFetcher,
)
from apps.repairs.tasks.fill_analytics.fetchers.kbrs_spo_fetcher import (
    BrigadeResolver,
    KbrsSPOFetcher,
    make_kbrs_brigade_resolver,
)
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.dependencies.db import get_aioboto_client_factory
from shared.integrations.abai.api.client import AbaiAsyncClient
from shared.integrations.kbrs.api.client import ToucanBackendClient

logger = get_logger(__name__)
settings = get_settings()


class FillRepairAnalytics:
    """Orchestrates one pass of analytics filling for all candidate repairs."""

    ITER_SIZE = 200
    GRACE_DAYS = 10

    def __init__(
        self,
        abai_client: AbaiAsyncClient | None = None,
        toucan_client: ToucanBackendClient | None = None,
        brigade_resolver: BrigadeResolver | None = None,
    ) -> None:
        self._abai_client = abai_client
        self._toucan_client = toucan_client
        self._brigade_resolver = brigade_resolver

    async def run(self) -> None:
        logger.info(
            "FillRepairAnalytics started (grace=%s days, batch=%s).",
            self.GRACE_DAYS,
            self.ITER_SIZE,
        )

        abai_client = self._abai_client or AbaiAsyncClient(
            username=settings.ABAI_LOGIN,
            password=settings.ABAI_PASS,
        )
        toucan_client = self._toucan_client
        storage = AiobotoFileStorage(
            bucket_name=settings.REPORTS_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
        )

        processed = 0
        finalized = 0
        now = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        grace_cutoff = now - timedelta(days=self.GRACE_DAYS)

        try:
            async with session_makers["app"]() as session:
                deps = _Dependencies.build(session)

                dyn_fetcher = AbaiDynamogramFetcher(
                    abai_client=abai_client,
                    storage=storage,
                    file_repo=deps.file_repo,
                    dynamogram_repo=deps.dynamogram_repo,
                )
                doc_fetcher = AbaiRepairDocFetcher(
                    abai_client=abai_client,
                    storage=storage,
                    file_repo=deps.file_repo,
                    repair_doc_repo=deps.doc_repo,
                )
                spo_fetcher = (
                    KbrsSPOFetcher(
                        toucan_client=toucan_client,
                        storage=storage,
                        file_repo=deps.file_repo,
                        spo_repo=deps.spo_repo,
                        summary_repo=deps.summary_repo,
                        brigade_resolver=(
                            self._brigade_resolver
                            or make_kbrs_brigade_resolver(toucan_client)
                        ),
                    )
                    if toucan_client is not None
                    else None
                )

                async for repairs in self._iter_candidates(session, grace_cutoff):
                    logger.debug("Batch: %s candidate repairs.", len(repairs))
                    for repair in repairs:
                        try:
                            did_finalize = await self._process_repair(
                                repair=repair,
                                deps=deps,
                                dyn_fetcher=dyn_fetcher,
                                doc_fetcher=doc_fetcher,
                                spo_fetcher=spo_fetcher,
                                grace_cutoff=grace_cutoff,
                            )
                            await session.commit()
                            processed += 1
                            if did_finalize:
                                finalized += 1
                        except Exception:
                            logger.exception(
                                "Failed processing repair id=%s; rolling back.",
                                repair.id,
                            )
                            await session.rollback()

            logger.info(
                "FillRepairAnalytics done. Processed=%s, finalized=%s.",
                processed,
                finalized,
            )
        finally:
            await abai_client.aclose()
            if toucan_client is not None:
                toucan_client.close()

    async def _process_repair(  # noqa: PLR0913
        self,
        *,
        repair: Repair,
        deps: "_Dependencies",
        dyn_fetcher: AbaiDynamogramFetcher,
        doc_fetcher: AbaiRepairDocFetcher,
        spo_fetcher: KbrsSPOFetcher | None,
        grace_cutoff: datetime,
    ) -> bool:
        well = (
            await deps.well_repo.get_by_id(id_=repair.well_id)
            if repair.well_id is not None
            else None
        )
        abai_well_id = well.abai_id if well else repair.abai_well_id

        analytics = await self._ensure_analytics(repair, deps.analytics_repo)

        if abai_well_id is not None:
            before, after = await dyn_fetcher.fetch_before_after(repair, abai_well_id)
            await self._link_dynamograms(
                analytics_id=analytics.id,
                before_id=before.id if before else None,
                after_id=after.id if after else None,
                analytics_dyn_repo=deps.analytics_dyn_repo,
            )

            doc = await doc_fetcher.fetch(repair, abai_well_id)
            if doc is not None and analytics.repair_docs_id != doc.id:
                await deps.analytics_repo.update_by_repair_id(
                    repair_id=repair.id,
                    data=UpdateRepairAnalyticsDTO(repair_docs_id=doc.id),
                )

        if spo_fetcher is not None:
            spos = await spo_fetcher.fetch_for_repair(repair)
            spo = spos[0] if spos else None
            if spo is not None:
                await self._link_spo(
                    analytics_id=analytics.id,
                    spo_id=spo.id,
                    analytics_spo_repo=deps.analytics_spo_repo,
                )

        if await self._should_finalize(repair, deps.doc_repo, grace_cutoff):
            await deps.analytics_repo.update_by_repair_id(
                repair_id=repair.id,
                data=UpdateRepairAnalyticsDTO(is_finalized=True),
            )
            return True
        return False

    async def _iter_candidates(
        self,
        session: AsyncSession,
        grace_cutoff: datetime,
    ) -> AsyncIterator[Sequence[Repair]]:
        last_id = 0
        while True:
            qs = (
                select(Repair)
                .outerjoin(
                    RepairAnalytics,
                    RepairAnalytics.repair_id == Repair.id,
                )
                .where(
                    Repair.id > last_id,
                    or_(
                        RepairAnalytics.is_finalized.is_(None),
                        RepairAnalytics.is_finalized.is_(False),
                    ),
                    or_(
                        Repair.end_time.is_(None),
                        Repair.end_time >= grace_cutoff,
                        RepairAnalytics.id.is_(None),
                    ),
                )
                .order_by(Repair.id.asc())
                .limit(self.ITER_SIZE)
            )
            result = await session.execute(qs)
            repairs = result.scalars().all()
            if not repairs:
                break
            yield repairs
            last_id = repairs[-1].id
            if len(repairs) < self.ITER_SIZE:
                break

    @staticmethod
    async def _ensure_analytics(
        repair: Repair,
        analytics_repo: RepairAnalyticsRepository,
    ) -> RepairAnalytics:
        existing = await analytics_repo.get_by_repair_id(repair.id)
        if existing is not None:
            return existing
        return await analytics_repo.create(
            CreateRepairAnalyticsDTO(repair_id=repair.id),
        )

    @staticmethod
    async def _link_dynamograms(
        *,
        analytics_id: int,
        before_id: int | None,
        after_id: int | None,
        analytics_dyn_repo: RepairAnalyticsDynamogramRepository,
    ) -> None:
        link = await analytics_dyn_repo.get_by_analytics_id(analytics_id)
        if link is None:
            if before_id is None and after_id is None:
                return
            await analytics_dyn_repo.create(
                CreateRepairAnalyticsDynamogramDTO(
                    analytics_id=analytics_id,
                    dynamogram_before_id=before_id,
                    dynamogram_after_id=after_id,
                ),
            )
            return

        update = UpdateRepairAnalyticsDynamogramDTO()
        if before_id is not None and link.dynamogram_before_id != before_id:
            update.dynamogram_before_id = before_id
        if after_id is not None and link.dynamogram_after_id != after_id:
            update.dynamogram_after_id = after_id
        if update.model_dump(exclude_unset=True):
            await analytics_dyn_repo.update_by_analytics_id(
                analytics_id=analytics_id,
                data=update,
            )

    @staticmethod
    async def _link_spo(
        *,
        analytics_id: int,
        spo_id: int,
        analytics_spo_repo: RepairAnalyticsSPORepository,
    ) -> None:
        link = await analytics_spo_repo.get_by_analytics_id(analytics_id)
        if link is None:
            await analytics_spo_repo.create(
                CreateRepairAnalyticsSPODTO(
                    analytics_id=analytics_id,
                    spo_id=spo_id,
                ),
            )
            return
        if link.spo_id != spo_id:
            await analytics_spo_repo.update_by_analytics_id(
                analytics_id=analytics_id,
                data=UpdateRepairAnalyticsSPODTO(spo_id=spo_id),
            )

    @classmethod
    async def _should_finalize(
        cls,
        repair: Repair,
        doc_repo: RepairDocRepository,
        grace_cutoff: datetime,
    ) -> bool:
        if repair.end_time is not None and repair.end_time < grace_cutoff:
            return True
        doc = await doc_repo.get_by_repair_id(repair.id)
        return (
            doc is not None
            and doc.por_file_id is not None
            and doc.act_file_id is not None
        )


class _Dependencies:
    __slots__ = (
        "analytics_dyn_repo",
        "analytics_repo",
        "analytics_spo_repo",
        "doc_repo",
        "dynamogram_repo",
        "file_repo",
        "spo_repo",
        "summary_repo",
        "well_repo",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        analytics_repo: RepairAnalyticsRepository,
        analytics_dyn_repo: RepairAnalyticsDynamogramRepository,
        analytics_spo_repo: RepairAnalyticsSPORepository,
        doc_repo: RepairDocRepository,
        dynamogram_repo: DynamogramRepository,
        spo_repo: SPORepository,
        summary_repo: RepairSummaryRepository,
        file_repo: FileRepository,
        well_repo: WellRepository,
    ) -> None:
        self.analytics_repo = analytics_repo
        self.analytics_dyn_repo = analytics_dyn_repo
        self.analytics_spo_repo = analytics_spo_repo
        self.doc_repo = doc_repo
        self.dynamogram_repo = dynamogram_repo
        self.spo_repo = spo_repo
        self.summary_repo = summary_repo
        self.file_repo = file_repo
        self.well_repo = well_repo

    @classmethod
    def build(cls, session: AsyncSession) -> "_Dependencies":
        return cls(
            analytics_repo=RepairAnalyticsRepository(session),
            analytics_dyn_repo=RepairAnalyticsDynamogramRepository(session),
            analytics_spo_repo=RepairAnalyticsSPORepository(session),
            doc_repo=RepairDocRepository(session),
            dynamogram_repo=DynamogramRepository(session),
            spo_repo=SPORepository(session),
            summary_repo=RepairSummaryRepository(session),
            file_repo=FileRepository(session),
            well_repo=WellRepository(session),
        )


async def main() -> None:
    await FillRepairAnalytics().run()


if __name__ == "__main__":
    asyncio.run(main())
