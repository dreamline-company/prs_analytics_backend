"""Разовая массовая заливка истории SDMO fc_data одного НГДУ.

Стратегия:
  1. Обновить справочники (fc_reg, stations) upsert'ом.
  2. Разбить станции на N шардов, сбалансированных ПО ЧИСЛУ СТРОК (жадный LPT).
  3. N процессов (multiprocessing, spawn) — каждый COPY'ит свои станции.
  4. ANALYZE в конце.

Индексы fc_data общие для всех НГДУ, поэтому по умолчанию они остаются на
месте: их снос ради заливки нового НГДУ оставил бы матрицу, карточку и
детекторы уже загруженных НГДУ без индексов на часы. ``--rebuild-indexes``
(снести перед заливкой, построить после — в разы быстрее) допустим только на
пустой таблице или в окне обслуживания.

Запуск (--stations, --workers и --rebuild-indexes опциональны):
    python -m apps.telemetry.tasks.load_sdmo.bulk_load --ngdu 9 --workers 4

Инкремент этим НЕ пользуется (incremental_load.py). На время заливки bulk
держит замок НГДУ (тот же, что у инкремента) и продлевает его: инкремент по
beat в это время пропускает прогоны и продолжит от курсора после bulk. Если
замок остался от упавшего процесса, он истечёт сам (BULK_LOCK_TTL_SEC) или
снимается вручную: ``redis-cli DEL sdmo:incremental:<abai_ngdu_id>``.
"""

import argparse
import asyncio
import multiprocessing
import os
import time

from sqlalchemy import func, select

from apps.models_registry import *  # noqa: F403
from apps.telemetry.repositories import SdmoFcDataRepository, SdmoStationRepository
from apps.telemetry.tasks.load_sdmo import indexes, loader
from apps.telemetry.tasks.load_sdmo.lock import (
    BULK_LOCK_TTL_SEC,
    LOCK_REFRESH_SEC,
    acquire_ngdu_load_lock,
    refresh_ngdu_load_lock,
    release_ngdu_load_lock,
)
from apps.telemetry.tasks.load_sdmo.sources import as_ngdu, sdmo_session_maker
from core import get_logger
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import engines, sdmo_engine_key, session_makers
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


async def _dispose_engines(ngdu: AbaiNGDUIDsEnum) -> None:
    # Движки привязаны к event loop, в котором их впервые использовали —
    # освобождаем перед следующим asyncio.run (иначе конфликт loop'ов).
    await engines["app"].dispose()
    await engines[sdmo_engine_key(ngdu)].dispose()


async def _station_row_counts(
    ngdu: AbaiNGDUIDsEnum,
    sdmo_ids: list[int],
) -> dict[int, int]:
    """Число строк fc_data на станцию (натуральный id) из источника."""
    if not sdmo_ids:
        return {}
    async with sdmo_session_maker(ngdu)() as sdmo_session:
        stmt = (
            select(FcDataDayParted.station_id, func.count())
            .where(FcDataDayParted.station_id.in_(sdmo_ids))
            .group_by(FcDataDayParted.station_id)
        )
        rows = (await sdmo_session.execute(stmt)).all()
    return dict(rows)


async def _prepare(
    ngdu: AbaiNGDUIDsEnum,
    station_sdmo_ids: list[int] | None,
    workers: int,
    *,
    rebuild_indexes: bool,
) -> list[tuple[list[int], int]]:
    """Справочники + балансировка шардов (родительский процесс).

    Возвращает пары (шард локальных station.id, ожидаемое число строк) — число
    нужно воркеру для прогресса в процентах и ETA.
    """
    async with (
        session_makers["app"]() as app_session,
        sdmo_session_maker(ngdu)() as sdmo_session,
    ):
        stations = await loader.load_dimensions(app_session, sdmo_session, ngdu)
    stations = loader.select_stations(stations, station_sdmo_ids)
    local_by_sdmo = {station.sdmo_id: station.id for station in stations}

    source_counts = await _station_row_counts(ngdu, list(local_by_sdmo))
    counts = {local_by_sdmo[sdmo_id]: n for sdmo_id, n in source_counts.items()}
    logger.info(
        "Bulk plan [%s]: %s stations, %s rows total",
        ngdu.name,
        len(counts),
        f"{sum(counts.values()):,}",
    )
    if rebuild_indexes:
        logger.warning(
            "Dropping fc_data secondary indexes for the whole table: queries of "
            "already loaded NGDUs degrade until the rebuild at the end",
        )
        await indexes.drop_secondary_indexes()
    shards = balance_shards(counts, workers)
    await _dispose_engines(ngdu)
    return [(shard, sum(counts[s] for s in shard)) for shard in shards]


async def _finalize(*, rebuild_indexes: bool) -> None:
    """Индексы (если сносили) + ANALYZE (родительский процесс, после воркеров)."""
    if rebuild_indexes:
        await indexes.create_secondary_indexes()
    await indexes.analyze_table()
    await engines["app"].dispose()


async def _worker_async(
    abai_ngdu_id: int,
    station_ids: list[int],
    shard_total: int,
) -> int:
    ngdu = as_ngdu(abai_ngdu_id)
    pid = os.getpid()
    logger.info(
        "worker pid=%s [%s]: %s stations, ~%s rows",
        pid,
        ngdu.name,
        len(station_ids),
        f"{shard_total:,}",
    )
    started = time.monotonic()
    async with (
        session_makers["app"]() as app_session,
        sdmo_session_maker(ngdu)() as sdmo_session,
    ):
        stations = await SdmoStationRepository(app_session).list_by_ids(station_ids)
        cursor = await SdmoFcDataRepository(
            app_session,
        ).get_last_cursor_by_station(station_ids)
        total = 0
        for station in stations:
            total += await loader.load_station_delta(
                app_session,
                sdmo_session,
                station,
                cursor.get(station.id, (None, 0)),
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
    await _dispose_engines(ngdu)
    return total


def _worker(abai_ngdu_id: int, station_ids: list[int], shard_total: int) -> int:
    """Точка входа дочернего процесса (spawn): свой event loop и свои движки."""
    try:
        return asyncio.run(_worker_async(abai_ngdu_id, station_ids, shard_total))
    except Exception:
        logger.exception("Worker failed on stations %s", station_ids[:5])
        raise


def _run_workers(
    ngdu: AbaiNGDUIDsEnum,
    plan: list[tuple[list[int], int]],
    token: str,
) -> int:
    """Прогнать шарды в пуле процессов, продлевая замок НГДУ, пока они работают."""
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(len(plan)) as pool:
        result = pool.starmap_async(
            _worker,
            [(int(ngdu), shard, shard_total) for shard, shard_total in plan],
        )
        while not result.ready():
            result.wait(LOCK_REFRESH_SEC)
            if not asyncio.run(
                refresh_ngdu_load_lock(int(ngdu), token, ttl_sec=BULK_LOCK_TTL_SEC),
            ):
                logger.error(
                    "Bulk load [%s]: NGDU lock lost (expired and taken over); "
                    "incremental load may now collide with workers",
                    ngdu.name,
                )
        return sum(result.get())


def run_bulk(
    abai_ngdu_id: int,
    station_sdmo_ids: list[int] | None = None,
    workers: int | None = None,
    *,
    rebuild_indexes: bool = False,
) -> None:
    ngdu = as_ngdu(abai_ngdu_id)
    workers = workers or os.cpu_count() or 4
    started = time.monotonic()

    token = asyncio.run(acquire_ngdu_load_lock(int(ngdu), ttl_sec=BULK_LOCK_TTL_SEC))
    if token is None:
        logger.error(
            "Bulk load [%s] aborted: another SDMO load of this NGDU holds the lock "
            "(incremental in progress) — wait for it or stop beat and retry",
            ngdu.name,
        )
        return

    total = 0
    try:
        plan = asyncio.run(
            _prepare(ngdu, station_sdmo_ids, workers, rebuild_indexes=rebuild_indexes),
        )
        logger.info(
            "Bulk load [%s]: %s shards, %s workers",
            ngdu.name,
            len(plan),
            workers,
        )
        if plan:
            total = _run_workers(ngdu, plan, token)
        asyncio.run(_finalize(rebuild_indexes=rebuild_indexes))
    finally:
        asyncio.run(release_ngdu_load_lock(int(ngdu), token))

    elapsed = time.monotonic() - started
    rate = total / elapsed if elapsed else 0
    logger.info(
        "Bulk load [%s] done: %s rows in %.0fs (%.0f rows/s)",
        ngdu.name,
        total,
        elapsed,
        rate,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDMO fc_data bulk load")
    parser.add_argument(
        "--ngdu",
        type=int,
        required=True,
        choices=[int(ngdu) for ngdu in AbaiNGDUIDsEnum],
        help="ABAI id НГДУ (AbaiNGDUIDsEnum)",
    )
    parser.add_argument(
        "--stations",
        type=str,
        default=None,
        help="Натуральные sdmo_id станций через запятую (по умолчанию все)",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=None,
        help="Число процессов (по умолчанию число ядер)",
    )
    parser.add_argument(
        "--rebuild-indexes",
        action="store_true",
        help="Снести вторичные индексы fc_data перед заливкой и построить после "
        "(только пустая таблица или окно обслуживания)",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    station_ids = [int(x) for x in args.stations.split(",")] if args.stations else None
    run_bulk(
        args.ngdu,
        station_sdmo_ids=station_ids,
        workers=args.workers,
        rebuild_indexes=args.rebuild_indexes,
    )


if __name__ == "__main__":
    main()
