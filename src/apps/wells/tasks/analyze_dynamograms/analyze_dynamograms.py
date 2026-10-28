"""ИИ-разбор динамограмм всего фонда.

    python -m apps.wells.tasks.analyze_dynamograms.analyze_dynamograms
    python -m apps.wells.tasks.analyze_dynamograms.analyze_dynamograms --well-id 4689
    python -m apps.wells.tasks.analyze_dynamograms.analyze_dynamograms --limit 200

Celery: ``wells.dynamograms.analyze`` каждые полчаса, под замком и с бюджетом
времени — недоделанное ждёт следующего прохода. Порядок — от свежих к
старым: новые динамограммы разбираются первыми, история догоняется в фоне.

Второй писатель тех же строк — аналитика ремонта (пары до/после). Оба идут
через ``DynamogramAnalyzer``: правила «разбирать ли» общие, одновременную
запись одной динамограммы решает upsert.
"""

import argparse
import asyncio
import time
from dataclasses import dataclass
from datetime import datetime

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.repairs.models.analytics import AI_STATUS_COMPLETED
from apps.repairs.repositories.ai_results import RepairDynamogramAIResultRepository
from apps.repairs.tasks.fetch_sources.clients import build_storage
from apps.repairs.tasks.fetch_sources.redis_utils import redis_lock
from apps.repairs.tasks.fill_analytics.ai.dynamogram_analyzer import (
    FAILED_RETRY_AFTER,
    INPUT_REJECTED_PREFIX,
    build_dynamogram_analyzer,
    build_dynamogram_processor,
)
from apps.repairs.tasks.fill_analytics.ai.dynamogram_processor import (
    DynamogramAIProcessor,
)
from apps.wells.models.dynamogram import Dynamogram
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

ANALYZE_TASK = "wells.dynamograms.analyze"
LOCK_KEY = "wells:dynamograms:analyze"
LOCK_TTL_SEC = 30 * 60
# Проход останавливается за 5 минут до soft-лимита celery (25 минут).
TIME_BUDGET_SEC = 20 * 60
PAGE_SIZE = 100
# Каждая динамограмма держит сессию БД на время вызова LLM, а пул app-движка —
# pool_size 5 + max_overflow 10; выше — упрёмся в пул, а не ускоримся.
DEFAULT_CONCURRENCY = 4
MAX_CONCURRENCY = 10


@dataclass(slots=True)
class RunResult:
    completed: int = 0
    # Упал разбор или отвергнут файл (HTML вместо картинки и т.п.).
    failed: int = 0
    # Исключение вне анализатора (БД, S3) — строка не записана.
    errors: int = 0
    # Динамограмму в этот момент разбирает аналитика ремонта.
    busy: int = 0
    tokens: int = 0
    # Остались необработанные динамограммы — кончился бюджет или лимит.
    deferred: bool = False


class AnalyzeDynamograms:
    def __init__(
        self,
        *,
        well_id: int | None = None,
        limit: int | None = None,
        concurrency: int = DEFAULT_CONCURRENCY,
        time_budget_sec: float | None = None,
    ) -> None:
        self._well_id = well_id
        self._limit = limit
        self._concurrency = max(1, min(concurrency, MAX_CONCURRENCY))
        self._time_budget_sec = time_budget_sec
        self._started = 0.0

    async def run(self) -> RunResult:
        self._started = time.monotonic()
        processor = build_dynamogram_processor()
        storage = build_storage()
        semaphore = asyncio.Semaphore(self._concurrency)
        failed_before = _local_now() - FAILED_RETRY_AFTER
        result = RunResult()
        after: tuple[datetime, int] | None = None
        taken = 0
        logger.info(
            "Dynamogram analysis started (well_id=%s, limit=%s, concurrency=%s, "
            "prompt=%s).",
            self._well_id,
            self._limit,
            self._concurrency,
            processor.prompt_version,
        )
        while True:
            page_size = PAGE_SIZE
            if self._limit is not None:
                page_size = min(PAGE_SIZE, self._limit - taken)
                if page_size <= 0:
                    break
            if self._budget_exhausted():
                result.deferred = True
                break
            async with session_makers["app"]() as session:
                page = await RepairDynamogramAIResultRepository(
                    session,
                ).list_dynamograms_to_analyze(
                    prompt_version=processor.prompt_version,
                    failed_before=failed_before,
                    rejected_error_prefix=INPUT_REJECTED_PREFIX,
                    after=after,
                    limit=page_size,
                    well_id=self._well_id,
                )
            if not page:
                break
            last = page[-1][0]
            after = (last.snapshot_time, last.id)
            taken += len(page)
            outcomes = await asyncio.gather(
                *(
                    self._analyze_one(
                        dynamogram,
                        abai_well_id=abai_well_id,
                        processor=processor,
                        storage=storage,
                        semaphore=semaphore,
                    )
                    for dynamogram, abai_well_id in page
                ),
            )
            self._count(result, outcomes)
            logger.info(
                "Dynamogram analysis: %s taken, completed=%s, failed=%s, errors=%s, "
                "busy=%s, tokens=%s; last snapshot %s.",
                taken,
                result.completed,
                result.failed,
                result.errors,
                result.busy,
                result.tokens,
                last.snapshot_time.date(),
            )
            if result.deferred:
                break
        if self._limit is not None and taken >= self._limit:
            result.deferred = True
        logger.info(
            "Dynamogram analysis done: completed=%s, failed=%s, errors=%s, "
            "busy=%s, tokens=%s, deferred=%s.",
            result.completed,
            result.failed,
            result.errors,
            result.busy,
            result.tokens,
            result.deferred,
        )
        return result

    async def _analyze_one(
        self,
        dynamogram: Dynamogram,
        *,
        abai_well_id: int | None,
        processor: DynamogramAIProcessor,
        storage: AiobotoFileStorage,
        semaphore: asyncio.Semaphore,
    ) -> tuple[str, int] | None:
        """(статус строки, токены); ``None`` — не начата из-за бюджета;
        ("error", 0) — исключение; ("busy", 0) — её держит аналитика ремонта."""
        async with semaphore:
            if self._budget_exhausted():
                return None
            try:
                async with session_makers["app"]() as session:
                    analyzer = build_dynamogram_analyzer(
                        session,
                        storage=storage,
                        processor=processor,
                    )
                    row, usage = await analyzer.analyze(
                        dynamogram,
                        abai_well_id=abai_well_id,
                        wait_for_lock=False,
                    )
                    await session.commit()
            except Exception:
                logger.exception("Dynamogram id=%s analysis failed.", dynamogram.id)
                return "error", 0
        if row is None:
            return "busy", 0
        return row.status, usage.total_tokens

    @staticmethod
    def _count(result: RunResult, outcomes: list[tuple[str, int] | None]) -> None:
        for outcome in outcomes:
            if outcome is None:
                result.deferred = True
                continue
            status, tokens = outcome
            result.tokens += tokens
            if status == "error":
                result.errors += 1
            elif status == "busy":
                result.busy += 1
            elif status == AI_STATUS_COMPLETED:
                result.completed += 1
            else:
                result.failed += 1

    def _budget_exhausted(self) -> bool:
        return (
            self._time_budget_sec is not None
            and time.monotonic() - self._started >= self._time_budget_sec
        )


def _local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


async def _sweep() -> None:
    async with redis_lock(LOCK_KEY, ttl_sec=LOCK_TTL_SEC) as acquired:
        if not acquired:
            logger.info("Dynamogram analysis already running; skipped.")
            return
        await AnalyzeDynamograms(time_budget_sec=TIME_BUDGET_SEC).run()


@celery_app.task(name=ANALYZE_TASK)
def analyze_dynamograms() -> None:
    run_async(_sweep())


async def main(
    *,
    well_id: int | None = None,
    limit: int | None = None,
    concurrency: int = DEFAULT_CONCURRENCY,
) -> None:
    await AnalyzeDynamograms(
        well_id=well_id,
        limit=limit,
        concurrency=concurrency,
    ).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AI analysis of dynamograms without a current result.",
    )
    parser.add_argument("--well-id", type=int, help="Only this well (wells_well.id).")
    parser.add_argument("--limit", type=int, help="Stop after N dynamograms.")
    parser.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"Parallel LLM calls (max {MAX_CONCURRENCY}).",
    )
    args = parser.parse_args()
    asyncio.run(
        main(well_id=args.well_id, limit=args.limit, concurrency=args.concurrency),
    )
