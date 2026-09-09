"""Аналитика ремонта по уже собранным данным.

Во внешние источники отсюда не ходим: ПОР/акты, динамограммы, СПО и путёвки
складывают отдельные таски ``apps.repairs.tasks.fetch_sources``. Здесь —
выбор входов из БД, LLM-разборы, KPI и финализация.

Запуск:
    python -m apps.repairs.tasks.fill_analytics.fill_repair_analytics
    python -m apps.repairs.tasks.fill_analytics.fill_repair_analytics --repair-id N
    python -m apps.repairs.tasks.fill_analytics.fill_repair_analytics --well-id N

Celery:
  * ``repairs.analytics.run_repair`` — один ремонт по событию добытчика
    (debounce в триггере), под замком ремонта;
  * ``repairs.analytics.sweep`` — часовой проход по кандидатам под общим
    замком и с бюджетом времени: недоделанные ремонты дожидаются следующего.

Входы ремонта: динамограммы до/после (``repairs_dynamogram``: ближайшая к
началу и ближайшая после конца), документ (``repairs_repair_docs``), СПО
скважины в окне ремонта (``repairs_spo``). Общий вердикт пересчитывается,
только если изменился отпечаток входов: данные приходят асинхронно, и без
этого вердикт по неполным данным застыл бы навсегда. LLM по СПО работает
только с закрытыми замерами (прибор перестал писать).

Отбор кандидатов и финализация — см. ``fetch_sources.candidates``:
  * кандидат — не финализирован и (активен, или в окне 10 дней после конца,
    или без аналитики);
  * финализация — end_time + 10 дней прошли, либо есть ПОР и акт, а общий
    вердикт ``completed``.
"""

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.files.repositories.file import FileRepository
from apps.kbrs.repositories.measure import KbrsMeasureRepository
from apps.models_registry import *  # noqa: F403
from apps.org.repositories import UniqueBrigadeRepository
from apps.repairs.dto.internal.repositories.analytics import (
    CreateRepairAnalyticsDTO,
    CreateRepairAnalyticsDynamogramDTO,
    CreateRepairAnalyticsSPODTO,
    UpdateRepairAnalyticsDTO,
    UpdateRepairAnalyticsDynamogramDTO,
    UpdateRepairAnalyticsSPODTO,
)
from apps.repairs.models.analytics import AI_STATUS_COMPLETED, RepairAnalytics
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.kpi import RepairKPIRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.tasks.fetch_sources.candidates import (
    as_naive,
    grace_cutoff,
    is_measure_closed,
    iter_candidate_repairs,
    local_now,
    repair_window,
    resolve_repair_well,
    scope_label,
)
from apps.repairs.tasks.fetch_sources.clients import build_storage
from apps.repairs.tasks.fetch_sources.redis_utils import (
    acquire_lock,
    redis_lock,
    release_lock,
)
from apps.repairs.tasks.fetch_sources.triggers import (
    ANALYTICS_RUN_TASK,
    ANALYTICS_SWEEP_TASK,
)
from apps.repairs.tasks.fill_analytics.ai.agent_factory import (
    build_dynamogram_agent,
    build_kpi_por_agent,
    build_kpi_spo_analysis_agent,
    build_overall_agent,
    build_spo_agent,
)
from apps.repairs.tasks.fill_analytics.ai.coordinator import AICoordinator
from apps.repairs.tasks.fill_analytics.ai.dynamogram_processor import (
    DynamogramAIProcessor,
)
from apps.repairs.tasks.fill_analytics.ai.overall_processor import OverallAIProcessor
from apps.repairs.tasks.fill_analytics.ai.spo_processor import SPOAIProcessor
from apps.repairs.tasks.fill_analytics.inputs import RepairInputs
from apps.repairs.tasks.fill_analytics.kpi.por_confirmation_processor import (
    PORConfirmationProcessor,
)
from apps.repairs.tasks.fill_analytics.kpi.repair_kpi_calculator import (
    RepairKPICalculator,
)
from apps.repairs.tasks.fill_analytics.kpi.spo_analysis_processor import (
    SPOAnalysisProcessor,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.models.spo import SPO
from apps.wells.models.well import Well
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.spo_event import SPOEventRepository
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

logger = get_logger(__name__)
settings = get_settings()

# Замок ремонта: событийный запуск и sweep не должны считать один ремонт
# одновременно. TTL — общий лимит celery-таски.
REPAIR_LOCK_TTL_SEC = 30 * 60
SWEEP_LOCK_KEY = "repairs:analytics:sweep"
# Sweep останавливается за 5 минут до soft-лимита: текущий ремонт дорабатывает,
# остальные ждут следующего часа.
SWEEP_TIME_BUDGET_SEC = 20 * 60


def repair_lock_key(repair_id: int) -> str:
    return f"repairs:analytics:lock:{repair_id}"


@dataclass(slots=True)
class RunResult:
    processed: int = 0
    finalized: int = 0
    skipped_locked: int = 0
    failed: int = 0
    # Остались необработанные кандидаты — кончился бюджет времени.
    deferred: bool = False


class FillRepairAnalytics:
    """Один проход аналитики по ремонтам: все кандидаты или явная область."""

    def __init__(
        self,
        *,
        repair_id: int | None = None,
        well_id: int | None = None,
        time_budget_sec: float | None = None,
    ) -> None:
        if repair_id is not None and well_id is not None:
            msg = "Pass either repair_id or well_id, not both."
            raise ValueError(msg)
        self._repair_id = repair_id
        self._well_id = well_id
        self._time_budget_sec = time_budget_sec

    async def run(self) -> RunResult:
        result = RunResult()
        now = local_now()
        cutoff = grace_cutoff(now)
        started = time.monotonic()
        logger.info(
            "FillRepairAnalytics started (scope=%s, budget=%s).",
            scope_label(repair_id=self._repair_id, well_id=self._well_id),
            self._time_budget_sec,
        )

        async with (
            session_makers["app"]() as session,
            session_makers["cm"]() as cm_session,
        ):
            deps = _Dependencies.build(session)
            ai_coordinator, kpi_calculator = self._build_processors(
                deps,
                session=session,
                cm_session=cm_session,
            )
            async for repairs in iter_candidate_repairs(
                session,
                grace_cutoff=cutoff,
                repair_id=self._repair_id,
                well_id=self._well_id,
            ):
                for repair in repairs:
                    if self._budget_exhausted(started):
                        logger.info(
                            "Time budget exhausted; remaining candidates wait "
                            "for the next run.",
                        )
                        result.deferred = True
                        self._log_result(result)
                        return result
                    await self._process_locked(
                        repair,
                        session=session,
                        deps=deps,
                        ai_coordinator=ai_coordinator,
                        kpi_calculator=kpi_calculator,
                        now=now,
                        cutoff=cutoff,
                        result=result,
                    )
        self._log_result(result)
        return result

    def _budget_exhausted(self, started: float) -> bool:
        return (
            self._time_budget_sec is not None
            and time.monotonic() - started >= self._time_budget_sec
        )

    @staticmethod
    def _log_result(result: RunResult) -> None:
        logger.info(
            "FillRepairAnalytics done. Processed=%s, finalized=%s, "
            "skipped_locked=%s, failed=%s, deferred=%s.",
            result.processed,
            result.finalized,
            result.skipped_locked,
            result.failed,
            result.deferred,
        )

    async def _process_locked(  # noqa: PLR0913
        self,
        repair: Repair,
        *,
        session: AsyncSession,
        deps: "_Dependencies",
        ai_coordinator: AICoordinator,
        kpi_calculator: RepairKPICalculator,
        now: datetime,
        cutoff: datetime,
        result: RunResult,
    ) -> None:
        key = repair_lock_key(repair.id)
        token = await acquire_lock(key, ttl_sec=REPAIR_LOCK_TTL_SEC)
        if token is None:
            logger.info(
                "Repair id=%s is being processed elsewhere; skipped.",
                repair.id,
            )
            result.skipped_locked += 1
            return
        try:
            did_finalize = await self._process_repair(
                repair=repair,
                deps=deps,
                ai_coordinator=ai_coordinator,
                kpi_calculator=kpi_calculator,
                now=now,
                grace_cutoff=cutoff,
            )
            await session.commit()
        except Exception:
            logger.exception(
                "Failed processing repair id=%s; rolling back.",
                repair.id,
            )
            await session.rollback()
            result.failed += 1
        else:
            result.processed += 1
            if did_finalize:
                result.finalized += 1
        finally:
            await release_lock(key, token)

    async def _process_repair(  # noqa: PLR0913
        self,
        *,
        repair: Repair,
        deps: "_Dependencies",
        ai_coordinator: AICoordinator,
        kpi_calculator: RepairKPICalculator,
        now: datetime,
        grace_cutoff: datetime,
    ) -> bool:
        logger.info(
            "Processing repair id=%s abai_well_id=%s start=%s end=%s",
            repair.id,
            repair.abai_well_id,
            repair.start_time,
            repair.end_time,
        )
        well = await resolve_repair_well(repair, deps.well_repo)
        analytics = await self._ensure_analytics(repair, deps.analytics_repo)
        inputs = await self._collect_inputs(repair, well, deps, now=now)
        logger.info(
            "Repair id=%s inputs: well=%s before=%s after=%s doc=%s spos=%s closed=%s",
            repair.id,
            well.id if well else None,
            inputs.before.id if inputs.before else None,
            inputs.after.id if inputs.after else None,
            inputs.doc.id if inputs.doc else None,
            [spo.id for spo in inputs.spos],
            sorted(inputs.closed_spo_ids),
        )

        await self._link_dynamograms(
            analytics_id=analytics.id,
            before_id=inputs.before.id if inputs.before else None,
            after_id=inputs.after.id if inputs.after else None,
            analytics_dyn_repo=deps.analytics_dyn_repo,
        )
        if inputs.doc is not None and analytics.repair_docs_id != inputs.doc.id:
            await deps.analytics_repo.update_by_repair_id(
                repair_id=repair.id,
                data=UpdateRepairAnalyticsDTO(repair_docs_id=inputs.doc.id),
            )
        primary_spo = inputs.primary_spo
        if primary_spo is not None:
            await self._link_spo(
                analytics_id=analytics.id,
                spo_id=primary_spo.id,
                analytics_spo_repo=deps.analytics_spo_repo,
            )

        # AI: разборы по элементам питают общий вердикт.
        dyn_before_ai = (
            await ai_coordinator.process_dynamogram(
                dynamogram=inputs.before,
                role="before",
                repair_id=repair.id,
            )
            if inputs.before is not None
            else None
        )
        dyn_after_ai = (
            await ai_coordinator.process_dynamogram(
                dynamogram=inputs.after,
                role="after",
                repair_id=repair.id,
            )
            if inputs.after is not None
            else None
        )
        spo_ai_results = [
            await ai_coordinator.process_spo(spo=spo, repair_id=repair.id)
            for spo in inputs.closed_spos
        ]
        overall_ai = await ai_coordinator.process_overall(
            analytics_id=analytics.id,
            repair=repair,
            dynamogram_before=dyn_before_ai,
            dynamogram_after=dyn_after_ai,
            spo_results=spo_ai_results,
            inputs_fingerprint=inputs.fingerprint(),
        )

        usage = ai_coordinator.pop_repair_usage(repair.id)
        logger.info(
            "Repair id=%s AI tokens: input=%s output=%s total=%s",
            repair.id,
            usage.input_tokens,
            usage.output_tokens,
            usage.total_tokens,
        )

        await kpi_calculator.compute_and_store(
            analytics_id=analytics.id,
            repair=repair,
            dynamogram_before_ai=dyn_before_ai,
            dynamogram_after_ai=dyn_after_ai,
            well_id=well.id if well else None,
            spo_closed=inputs.primary_spo_closed,
            spo_revision=primary_spo.raw_size if primary_spo else None,
        )

        if await self._should_finalize(
            repair=repair,
            doc_repo=deps.doc_repo,
            grace_cutoff=grace_cutoff,
            overall_ai_status=overall_ai.status,
        ):
            await deps.analytics_repo.update_by_repair_id(
                repair_id=repair.id,
                data=UpdateRepairAnalyticsDTO(is_finalized=True),
            )
            return True
        return False

    @staticmethod
    async def _collect_inputs(
        repair: Repair,
        well: Well | None,
        deps: "_Dependencies",
        *,
        now: datetime,
    ) -> RepairInputs:
        before = after = None
        spos: list[SPO] = []
        if well is not None:
            before = await deps.dynamogram_repo.get_closest_before(
                well.id,
                as_naive(repair.start_time),
            )
            if repair.end_time is not None:
                after = await deps.dynamogram_repo.get_closest_after(
                    well.id,
                    as_naive(repair.end_time),
                )
            start, end = repair_window(repair, now=now)
            spos = list(
                await deps.spo_repo.list_by_well_id_in_window(well.id, start, end),
            )
        else:
            logger.warning(
                "Repair id=%s has no local well → dynamograms and SPO skipped.",
                repair.id,
            )
        doc = await deps.doc_repo.get_by_repair_id(repair.id)
        closed = await _closed_spo_ids(spos, deps.measure_repo, now=now)
        return RepairInputs(
            before=before,
            after=after,
            doc=doc,
            spos=spos,
            closed_spo_ids=closed,
        )

    @staticmethod
    def _build_processors(
        deps: "_Dependencies",
        *,
        session: AsyncSession,
        cm_session: AsyncSession,
    ) -> tuple[AICoordinator, RepairKPICalculator]:
        storage = build_storage()
        repair_brigade_repo = RepairBrigadeRepository(session)
        unique_brigade_repo = UniqueBrigadeRepository(session)
        cm_brigade_repo = CMBrigadeRepository(cm_session)
        cm_brigade_error_screen_repo = CMBrigadeErrorScreenRepository(cm_session)
        ai_coordinator = AICoordinator(
            dynamogram_processor=DynamogramAIProcessor(
                build_dynamogram_agent(),
                model_name=settings.LLM_MODEL_NAME,
            ),
            spo_processor=SPOAIProcessor(
                build_spo_agent(),
                model_name=settings.LLM_MODEL_NAME,
            ),
            overall_processor=OverallAIProcessor(
                build_overall_agent(),
                model_name=settings.LLM_MODEL_NAME,
            ),
            dynamogram_ai_repo=deps.dynamogram_ai_repo,
            spo_ai_repo=deps.spo_ai_repo,
            overall_ai_repo=deps.overall_ai_repo,
            file_repo=deps.file_repo,
            storage=storage,
            repair_brigade_repo=repair_brigade_repo,
            unique_brigade_repo=unique_brigade_repo,
            cm_brigade_repo=cm_brigade_repo,
            cm_brigade_error_screen_repo=cm_brigade_error_screen_repo,
        )
        kpi_calculator = RepairKPICalculator(
            kpi_repo=deps.kpi_repo,
            well_repo=deps.well_repo,
            summary_repo=deps.summary_repo,
            doc_repo=deps.doc_repo,
            analytics_spo_repo=deps.analytics_spo_repo,
            spo_event_repo=deps.spo_event_repo,
            file_repo=deps.file_repo,
            storage=storage,
            tech_regime_repo=deps.tech_regime_repo,
            telemetry_repo=deps.telemetry_repo,
            repair_brigade_repo=repair_brigade_repo,
            unique_brigade_repo=unique_brigade_repo,
            cm_brigade_repo=cm_brigade_repo,
            cm_brigade_error_screen_repo=cm_brigade_error_screen_repo,
            por_processor=PORConfirmationProcessor(
                build_kpi_por_agent(),
                model_name=settings.LLM_MODEL_NAME,
            ),
            spo_analysis_processor=SPOAnalysisProcessor(
                build_kpi_spo_analysis_agent(),
                model_name=settings.LLM_MODEL_NAME,
            ),
        )
        return ai_coordinator, kpi_calculator

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
        *,
        repair: Repair,
        doc_repo: RepairDocRepository,
        grace_cutoff: datetime,
        overall_ai_status: str,
    ) -> bool:
        # Ветка «документы собраны» требует и вердикт — иначе строка замёрзнет
        # до разбора. Ветка по сроку финализирует безусловно: окно вышло.
        docs_complete = await cls._docs_complete(repair.id, doc_repo)
        end_time = repair.end_time
        if end_time is not None and as_naive(end_time) < grace_cutoff:
            return True
        return docs_complete and overall_ai_status == AI_STATUS_COMPLETED

    @staticmethod
    async def _docs_complete(
        repair_id: int,
        doc_repo: RepairDocRepository,
    ) -> bool:
        doc = await doc_repo.get_by_repair_id(repair_id)
        return (
            doc is not None
            and doc.por_file_id is not None
            and doc.act_file_id is not None
        )


async def _closed_spo_ids(
    spos: list[SPO],
    measure_repo: KbrsMeasureRepository,
    *,
    now: datetime,
) -> frozenset[int]:
    """СПО, чьи замеры прибор уже не пишет.

    СПО без привязки к замеру опросчика (история, прямой Toucan) считаются
    закрытыми: другого признака у них нет.
    """
    measure_ids = [spo.kbrs_measure_id for spo in spos if spo.kbrs_measure_id]
    measures = await measure_repo.map_by_measure_ids(measure_ids)
    closed: set[int] = set()
    for spo in spos:
        if spo.kbrs_measure_id is None:
            closed.add(spo.id)
            continue
        measure = measures.get(spo.kbrs_measure_id)
        if measure is None or is_measure_closed(measure.end_time, now=now):
            closed.add(spo.id)
    return frozenset(closed)


@dataclass(slots=True)
class _Dependencies:
    analytics_repo: RepairAnalyticsRepository
    analytics_dyn_repo: RepairAnalyticsDynamogramRepository
    analytics_spo_repo: RepairAnalyticsSPORepository
    doc_repo: RepairDocRepository
    dynamogram_repo: DynamogramRepository
    spo_repo: SPORepository
    spo_event_repo: SPOEventRepository
    summary_repo: RepairSummaryRepository
    file_repo: FileRepository
    well_repo: WellRepository
    measure_repo: KbrsMeasureRepository
    dynamogram_ai_repo: RepairDynamogramAIResultRepository
    spo_ai_repo: RepairSPOAIResultRepository
    overall_ai_repo: RepairAIAnalysisRepository
    kpi_repo: RepairKPIRepository
    tech_regime_repo: TechRegimeRepository
    telemetry_repo: TelemetryRepository

    @classmethod
    def build(cls, session: AsyncSession) -> "_Dependencies":
        return cls(
            analytics_repo=RepairAnalyticsRepository(session),
            analytics_dyn_repo=RepairAnalyticsDynamogramRepository(session),
            analytics_spo_repo=RepairAnalyticsSPORepository(session),
            doc_repo=RepairDocRepository(session),
            dynamogram_repo=DynamogramRepository(session),
            spo_repo=SPORepository(session),
            spo_event_repo=SPOEventRepository(session),
            summary_repo=RepairSummaryRepository(session),
            file_repo=FileRepository(session),
            well_repo=WellRepository(session),
            measure_repo=KbrsMeasureRepository(session),
            dynamogram_ai_repo=RepairDynamogramAIResultRepository(session),
            spo_ai_repo=RepairSPOAIResultRepository(session),
            overall_ai_repo=RepairAIAnalysisRepository(session),
            kpi_repo=RepairKPIRepository(session),
            tech_regime_repo=TechRegimeRepository(session),
            telemetry_repo=TelemetryRepository(session),
        )


# --- Celery ------------------------------------------------------------------


@celery_app.task(name=ANALYTICS_RUN_TASK)
def run_repair_analytics(repair_id: int) -> None:
    """Аналитика одного ремонта по событию добытчика (см. triggers)."""
    run_async(FillRepairAnalytics(repair_id=repair_id).run())


async def _sweep() -> None:
    async with redis_lock(SWEEP_LOCK_KEY, ttl_sec=REPAIR_LOCK_TTL_SEC) as acquired:
        if not acquired:
            logger.info("Repair analytics sweep already running; skipped.")
            return
        await FillRepairAnalytics(time_budget_sec=SWEEP_TIME_BUDGET_SEC).run()


@celery_app.task(name=ANALYTICS_SWEEP_TASK)
def sweep_repair_analytics() -> None:
    """Страховочный проход по кандидатам: догоняет потерянные события."""
    run_async(_sweep())


# --- CLI ---------------------------------------------------------------------


async def main(
    *,
    repair_id: int | None = None,
    well_id: int | None = None,
) -> None:
    await FillRepairAnalytics(repair_id=repair_id, well_id=well_id).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Run repair analytics over collected data. "
        "Without args processes all candidates.",
    )
    scope = parser.add_mutually_exclusive_group()
    scope.add_argument(
        "--repair-id",
        type=int,
        help="Process only this repair (ignores finalization filter).",
    )
    scope.add_argument(
        "--well-id",
        type=int,
        help="Process all repairs of this well (ignores finalization filter).",
    )
    args = parser.parse_args()
    asyncio.run(main(repair_id=args.repair_id, well_id=args.well_id))
