"""Итоговое состояние скважин — каждые 5 минут, после загрузки СДМО.

Считает состояние каждой скважины всех НГДУ (ремонт, статус ABAI, привод по
СДМО, детекторы) и ведёт журнал ``wells_well_state``; матрица и карточка
только читают открытые строки. Логика — ``apps.wells.services.well_state``,
план — docs/well_state_plan.md.

Первый запуск (после миграции) — вручную: курсоры по отсчётам СДМО ещё не
заведены, и по каждой станции ищется последняя смена привода за 30 суток::

    python -m apps.wells.tasks.refresh_well_state.refresh_well_state --dry-run
    python -m apps.wells.tasks.refresh_well_state.refresh_well_state

``--dry-run`` считает всё и откатывает транзакции — для сводки до записи.
Повторный запуск идемпотентен: открытые строки обновляются, не дублируются.
"""

import argparse
import asyncio
from collections import Counter
from datetime import datetime

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.repairs.tasks.fetch_sources.redis_utils import redis_lock
from apps.wells.services.well_state.refresh import NgduResult, WellStateRefresher
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

LOCK_KEY = "wells:state:refresh"
# Дольше лимита таски замок не нужен: после гибели процесса следующий прогон
# не ждёт дольше.
LOCK_TTL_SEC = 15 * 60
SOFT_TIME_LIMIT_SEC = 10 * 60
TIME_LIMIT_SEC = 12 * 60


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


async def main(*, dry_run: bool = False) -> list[NgduResult]:
    async with redis_lock(LOCK_KEY, ttl_sec=LOCK_TTL_SEC) as acquired:
        if not acquired:
            logger.info("Well state refresh already running; skipped")
            return []
        async with session_makers["app"]() as session:
            return await WellStateRefresher(session, now=local_now()).run(
                dry_run=dry_run,
            )


@celery_app.task(
    name="wells.state.refresh",
    soft_time_limit=SOFT_TIME_LIMIT_SEC,
    time_limit=TIME_LIMIT_SEC,
)
def refresh_well_state() -> None:
    run_async(main())


def _print_summary(results: list[NgduResult], *, dry_run: bool) -> None:
    total: Counter = Counter()
    print("DRY RUN — ничего не записано" if dry_run else "Записано")  # noqa: T201
    for result in results:
        total.update(result.states)
        line = (
            f"НГДУ {result.abai_ngdu_id}: скважин {result.wells}, "
            f"состояния {dict(result.states)}, переходы {dict(result.transitions)}, "
            f"отсчётов СДМО {result.fc_rows}, станций инициализировано "
            f"{result.initialized_stations}"
        )
        if result.outage_since is not None:
            line += f", сбой СДМО с {result.outage_since:%d.%m %H:%M}"
        if result.error is not None:
            line += f", ОШИБКА {result.error}"
        print(line)  # noqa: T201
    print(f"Всего: {dict(total)}")  # noqa: T201


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Итоговое состояние скважин")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    _print_summary(asyncio.run(main(dry_run=args.dry_run)), dry_run=args.dry_run)
