"""Разовая массовая заливка истории SDMO fc_data на максимальной скорости.

Стратегия:
  1. Залить справочники (fc_reg, stations), снести вторичные индексы fc_data.
  2. Разбить станции на N шардов, сбалансированных ПО ЧИСЛУ СТРОК (жадный LPT).
  3. N процессов (multiprocessing, spawn) — каждый COPY'ит свои станции.
  4. Построить индексы один раз в конце + ANALYZE.

Запуск (--stations и --workers опциональны):
    python -m apps.telemetry.tasks.load_sdmo.bulk_load --workers 4

Инкремент этим НЕ пользуется — там индексы остаются на месте (incremental_load.py).
"""

import argparse
import asyncio
import multiprocessing
import os
import time

from sqlalchemy import func, select

from apps.models_registry import *  # noqa: F403
from apps.telemetry.repositories import SdmoFcDataRepository
from apps.telemetry.tasks.load_sdmo import indexes, loader
from core import get_logger
from shared.database.sql.setup import engines, session_makers
from shared.integrations.sdmo.models import FcDataDayParted

logger = get_logger(__name__)


def balance_shards(counts: dict[int, int], workers: int) -> list[list[int]]:
    """Разложить станции по ``workers`` шардам, балансируя суммарное число строк.

    Жадный LPT: станции по убыванию объёма, каждую — в самый лёгкий шард.
    """
    shards: list[list[int]] = [[] for _ in range(workers)]
    loads = [0] * workers
    for station_id, count in sorted(counts.items(), key=lambda kv: kv[1], reverse=True):
        lightest = min(range(workers), key=lambda i: loads[i])
        shards[lightest].append(station_id)
        loads[lightest] += count
    return [shard for shard in shards if shard]


async def _station_row_counts(station_ids: list[int] | None) -> dict[int, int]:
    """Число строк fc_data на станцию из источника (для балансировки шардов)."""
    async with session_makers["sdmo"]() as sdmo_session:
        stmt = select(
            FcDataDayParted.station_id,
            func.count(),
        ).group_by(FcDataDayParted.station_id)
        if station_ids:
            stmt = stmt.where(FcDataDayParted.station_id.in_(station_ids))
        rows = (await sdmo_session.execute(stmt)).all()
    return dict(rows)


async def _prepare(
    station_ids: list[int] | None,
    workers: int,
) -> list[tuple[list[int], int]]:
    """Справочники + снос индексов + балансировка шардов (родительский процесс).

    Возвращает пары (шард, ожидаемое число строк) — число нужно воркеру для
    прогресса в процентах и ETA.
    """
    async with (
        session_makers["app"]() as app_session,
        session_makers["sdmo"]() as sdmo_session,
    ):
        await loader.load_dimensions(app_session, sdmo_session)

    counts = await _station_row_counts(station_ids)
    logger.info(
        "Bulk plan: %s stations, %s rows total",
        len(counts),
        f"{sum(counts.values()):,}",
    )
    await indexes.drop_secondary_indexes()
    shards = balance_shards(counts, workers)
    # Движки родителя привязаны к этому event loop — освобождаем перед следующим
    # asyncio.run (иначе конфликт loop'ов); дочерние процессы создают свои.
    await engines["app"].dispose()
    await engines["sdmo"].dispose()
    return [(shard, sum(counts[s] for s in shard)) for shard in shards]


async def _finalize() -> None:
    """Построить индексы + ANALYZE (родительский процесс, после воркеров)."""
    await indexes.create_secondary_indexes()
    await indexes.analyze_table()
    await engines["app"].dispose()


async def _worker_async(station_ids: list[int], shard_total: int) -> int:
    pid = os.getpid()
    logger.info(
        "worker pid=%s: %s stations, ~%s rows",
        pid,
        len(station_ids),
        f"{shard_total:,}",
    )
    started = time.monotonic()
    async with (
        session_makers["app"]() as app_session,
        session_makers["sdmo"]() as sdmo_session,
    ):
        cursor = await SdmoFcDataRepository(
            app_session,
        ).get_last_cursor_by_station(station_ids)
        total = 0
        for station_id in station_ids:
            total += await loader.load_station_delta(
                app_session,
                sdmo_session,
                station_id,
                cursor.get(station_id, (None, 0)),
                label=f"[pid {pid}] ",
            )
            elapsed = time.monotonic() - started
            rate = total / elapsed if elapsed else 0
            pct = 100 * total / shard_total if shard_total else 0
            eta_min = (shard_total - total) / rate / 60 if rate else 0
            logger.info(
                "worker pid=%s: %s/%s rows (%.0f%%), %.0f rows/s, ETA %.0fm",
                pid,
                f"{total:,}",
                f"{shard_total:,}",
                pct,
                rate,
                eta_min,
            )
    await engines["app"].dispose()
    await engines["sdmo"].dispose()
    return total


def _worker(station_ids: list[int], shard_total: int) -> int:
    """Точка входа дочернего процесса (spawn): свой event loop и свои движки."""
    try:
        return asyncio.run(_worker_async(station_ids, shard_total))
    except Exception:
        logger.exception("Worker failed on stations %s", station_ids[:5])
        raise


def run_bulk(
    station_ids: list[int] | None = None,
    workers: int | None = None,
) -> None:
    workers = workers or os.cpu_count() or 4
    started = time.monotonic()

    plan = asyncio.run(_prepare(station_ids, workers))
    logger.info("Bulk load: %s shards, %s workers", len(plan), workers)

    total = 0
    if plan:
        ctx = multiprocessing.get_context("spawn")
        with ctx.Pool(len(plan)) as pool:
            total = sum(pool.starmap(_worker, plan))

    asyncio.run(_finalize())

    elapsed = time.monotonic() - started
    rate = total / elapsed if elapsed else 0
    logger.info(
        "Bulk load done: %s rows in %.0fs (%.0f rows/s)",
        total,
        elapsed,
        rate,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDMO fc_data bulk load")
    parser.add_argument(
        "--stations",
        type=str,
        default=None,
        help="Список station_id через запятую (по умолчанию все)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Число процессов (по умолчанию число ядер)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    station_ids = [int(x) for x in args.stations.split(",")] if args.stations else None
    run_bulk(station_ids=station_ids, workers=args.workers)


if __name__ == "__main__":
    main()
