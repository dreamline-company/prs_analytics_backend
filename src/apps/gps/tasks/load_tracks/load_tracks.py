"""Загрузка GPS-треков машин из «Аргуса» в ``gps_message``.

    python -m apps.gps.tasks.load_tracks.load_tracks
    python -m apps.gps.tasks.load_tracks.load_tracks --unit 243 \
        --from 2026-10-12 --to 2026-10-14
    python -m apps.gps.tasks.load_tracks.load_tracks --reread-hours 72

Celery: ``gps.tracks.incremental`` — каждые 15 минут и ночью с перечиткой 72 ч.

Какие машины: подъёмники и машины, чей номер встречается в путёвках УТО или
сводках ПРС за глубину истории (``tracked``, пересчёт в начале прогона).

Окно машины — ``[курсор − перечитка, now]``, у новой машины (курсора нет) —
``[now − HISTORY, now]``: первый прогон и есть бэкфилл. Окно режется на куски
по ``CHUNK``; после каждого куска точки вставляются с пропуском повторов и
курсор сдвигается на конец куска — оборванный прогон не теряет прогресс.
Курсор — граница запрошенного окна, а не время последней точки: иначе
молчащая машина перечитывала бы одно и то же.

Перечитка нужна потому, что трекер без связи копит точки и досылает их позже
(``received_at`` ≫ ``time``): каждый прогон перечитывает ``REREAD``, ночной —
72 ч. Окна подбираются по фактической задержке ``received_at − time``.

Прогон ограничен ``RUN_BUDGET``: дальше новые куски не берутся, остаток
догрузит следующий прогон (первый бэкфилл ~100 машин × 26 кусков идёт
несколько прогонов). Ручной запуск с явным периодом курсор не трогает.
"""

import argparse
import asyncio
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.celery_app import celery_app, run_async
from apps.gps.models.unit import GpsUnit
from apps.gps.repositories.gps import GpsMessageRepository, GpsUnitRepository
from apps.gps.services.parsing import message_dtos, to_unix, tracked_unit_ids
from apps.gps.services.plates import wanted_plates
from apps.gps.tasks.common import (
    TRACKS_LOAD_TASK,
    argus_configured,
    build_argus_client,
    local_now,
)
from apps.models_registry import *  # noqa: F403
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from apps.repairs.tasks.fetch_sources.redis_utils import redis_lock
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.argus.api import (
    ArgusClient,
    ArgusMessageDto,
    ArgusTransportError,
)

logger = get_logger(__name__)

# Глубина первой загрузки: Аргус хранит ~6 месяцев истории.
HISTORY = timedelta(days=180)
REREAD = timedelta(hours=2)
NIGHTLY_REREAD_HOURS = 72
# Кусок запроса: 7 суток подъёмника — сотни КБ, ~1 с.
CHUNK = timedelta(days=7)
RUN_BUDGET = timedelta(minutes=10)
# Одновременных запросов к Аргусу.
CONCURRENCY = 4
# Столько машин подряд упали на соединении — Аргус лежит, прогон прекращается.
MAX_TRANSPORT_FAILURES = 5

LOCK_KEY = "gps:tracks"
# Совпадает с task_time_limit celery.
LOCK_TTL_SEC = 30 * 60


@dataclass(slots=True)
class TracksLoadStats:
    units: int = 0
    loaded_units: int = 0
    chunks: int = 0
    points: int = 0
    inserted: int = 0
    failed: int = 0
    deferred: int = 0
    aborted: bool = False


def iter_chunks(
    start: datetime,
    end: datetime,
    step: timedelta = CHUNK,
) -> Iterator[tuple[datetime, datetime]]:
    cursor = start
    while cursor < end:
        chunk_end = min(cursor + step, end)
        yield cursor, chunk_end
        cursor = chunk_end


def track_window(
    cursor: datetime | None,
    *,
    now: datetime,
    reread: timedelta = REREAD,
    history: timedelta = HISTORY,
) -> tuple[datetime, datetime]:
    """Окно запроса машины: от курсора с перечиткой, без курсора — вся история."""
    floor = now - history
    if cursor is None:
        return floor, now
    return max(cursor - reread, floor), now


class LoadGpsTracks:
    def __init__(  # noqa: PLR0913
        self,
        *,
        argus_ids: Sequence[int] | None = None,
        period: tuple[datetime, datetime] | None = None,
        reread: timedelta = REREAD,
        history: timedelta = HISTORY,
        budget: timedelta | None = RUN_BUDGET,
        client: ArgusClient | None = None,
    ) -> None:
        self._argus_ids = list(argus_ids) if argus_ids else None
        self._period = period
        self._reread = reread
        self._history = history
        self._budget = budget
        self._client = client
        self._stats = TracksLoadStats()
        self._transport_failures = 0
        self._deadline: float | None = None
        self._active_client: ArgusClient | None = None

    async def run(self) -> TracksLoadStats:
        if self._client is None and not argus_configured():
            logger.error("GPS tracks load skipped: ARGUS_LOGIN/ARGUS_PASS not set.")
            return self._stats
        async with redis_lock(LOCK_KEY, ttl_sec=LOCK_TTL_SEC) as acquired:
            if not acquired:
                logger.info("GPS tracks load skipped: previous run holds the lock.")
                return self._stats
            await self._run()
        return self._stats

    async def _run(self) -> None:
        now = local_now()
        units = await self._select_units(now)
        self._stats.units = len(units)
        logger.info(
            "GPS tracks load started: units=%s reread=%s period=%s.",
            len(units),
            self._reread,
            self._period,
        )
        loop = asyncio.get_running_loop()
        if self._budget is not None:
            self._deadline = loop.time() + self._budget.total_seconds()
        self._active_client = self._client or build_argus_client()
        semaphore = asyncio.Semaphore(CONCURRENCY)
        try:
            await asyncio.gather(
                *(self._load_unit_guarded(unit, now, semaphore) for unit in units),
            )
        finally:
            if self._client is None:
                await self._active_client.aclose()
            self._active_client = None
        stats = self._stats
        logger.info(
            "GPS tracks load done: units=%s loaded=%s chunks=%s points=%s "
            "inserted=%s failed=%s deferred=%s aborted=%s.",
            stats.units,
            stats.loaded_units,
            stats.chunks,
            stats.points,
            stats.inserted,
            stats.failed,
            stats.deferred,
            stats.aborted,
        )

    async def _select_units(self, now: datetime) -> list[GpsUnit]:
        """Машины прогона; заодно пересчитывает ``tracked``.

        Сначала машины с курсором (короткий хвост), потом новые с бэкфиллом:
        долгая история новых не задерживает свежие точки остальных.
        """
        async with session_makers["app"]() as session:
            unit_repo = GpsUnitRepository(session)
            since = now - self._history
            plates = [
                *await RepairTransportRepository(session).list_vehicle_numbers_since(
                    since,
                ),
                *await RepairSummaryRepository(session).list_cars_since(since.date()),
            ]
            active = await unit_repo.list_active()
            tracked = tracked_unit_ids(
                [(unit.id, unit.plate_normalized, unit.is_lift) for unit in active],
                wanted_plates(plates),
            )
            enabled, disabled = await unit_repo.set_tracked(tracked)
            await session.commit()
            logger.info(
                "GPS tracked units: %s (enabled=%s disabled=%s).",
                len(tracked),
                enabled,
                disabled,
            )
            if self._argus_ids is not None:
                units = list(await unit_repo.list_by_argus_ids(self._argus_ids))
                missing = set(self._argus_ids) - {unit.argus_id for unit in units}
                if missing:
                    logger.warning("GPS units not in gps_unit: %s", sorted(missing))
            else:
                units = list(await unit_repo.list_tracked())
        units.sort(key=lambda unit: unit.track_cursor_at is None)
        return units

    async def _load_unit_guarded(
        self,
        unit: GpsUnit,
        now: datetime,
        semaphore: asyncio.Semaphore,
    ) -> None:
        async with semaphore:
            if self._stats.aborted:
                return
            try:
                await self._load_unit(unit, now)
            except ArgusTransportError as exc:
                # Трейсбек сетевой ошибки ничего не добавляет к причине.
                logger.warning("GPS track of %s: Argus down (%s).", unit.name, exc)
                self._stats.failed += 1
                self._transport_failures += 1
                if self._transport_failures >= MAX_TRANSPORT_FAILURES:
                    logger.error(  # noqa: TRY400
                        "GPS tracks load aborted: %s units in a row failed to connect.",
                        self._transport_failures,
                    )
                    self._stats.aborted = True
            except Exception:
                logger.exception("GPS track of %s failed; cursor not moved.", unit.name)
                self._stats.failed += 1

    def _out_of_budget(self) -> bool:
        return (
            self._deadline is not None
            and asyncio.get_running_loop().time() > self._deadline
        )

    async def _load_unit(self, unit: GpsUnit, now: datetime) -> None:
        if self._period is not None:
            start, end = self._period
        else:
            start, end = track_window(
                unit.track_cursor_at,
                now=now,
                reread=self._reread,
                history=self._history,
            )
        inserted = 0
        for chunk_start, chunk_end in iter_chunks(start, end):
            if self._stats.aborted:
                return
            if self._out_of_budget():
                self._stats.deferred += 1
                return
            messages = await self._fetch(unit, chunk_start, chunk_end)
            self._transport_failures = 0
            dtos = message_dtos(unit.id, messages, now=now)
            async with session_makers["app"]() as session:
                chunk_inserted = await GpsMessageRepository(session).insert_ignore(dtos)
                if self._period is None:
                    await GpsUnitRepository(session).advance_cursor(unit.id, chunk_end)
                await session.commit()
            inserted += chunk_inserted
            self._stats.chunks += 1
            self._stats.points += len(dtos)
            self._stats.inserted += chunk_inserted
        self._stats.loaded_units += 1
        logger.debug("GPS track of %s: +%s points.", unit.name, inserted)

    async def _fetch(
        self,
        unit: GpsUnit,
        start: datetime,
        end: datetime,
    ) -> list[ArgusMessageDto]:
        if self._active_client is None:
            msg = "Argus client is not open"
            raise RuntimeError(msg)
        return await self._active_client.get_unit_msgs(
            unit.argus_id,
            start=to_unix(start),
            end=to_unix(end),
        )


@celery_app.task(name=TRACKS_LOAD_TASK)
def load_gps_tracks(reread_hours: float | None = None) -> None:
    reread = timedelta(hours=reread_hours) if reread_hours else REREAD
    run_async(LoadGpsTracks(reread=reread).run())


def _parse_moment(value: str) -> datetime:
    """``2026-10-12`` или ``2026-10-12T10:00`` — местное время."""
    return datetime.fromisoformat(value)


async def main(args: argparse.Namespace) -> None:
    period = None
    if args.date_from is not None:
        end = args.date_to if args.date_to is not None else local_now()
        period = (args.date_from, end)
    await LoadGpsTracks(
        argus_ids=args.unit,
        period=period,
        reread=timedelta(hours=args.reread_hours),
        budget=None,
    ).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load GPS tracks from Argus.")
    parser.add_argument(
        "--unit",
        type=int,
        action="append",
        help="id объекта в Аргусе (argus_id); можно несколько раз",
    )
    parser.add_argument(
        "--from",
        dest="date_from",
        type=_parse_moment,
        help="начало периода, местное; с периодом курсор не двигается",
    )
    parser.add_argument(
        "--to",
        dest="date_to",
        type=_parse_moment,
        help="конец периода, местное (по умолчанию — сейчас)",
    )
    parser.add_argument(
        "--reread-hours",
        type=float,
        default=REREAD.total_seconds() / 3600,
    )
    cli_args = parser.parse_args()
    if cli_args.date_to is not None and cli_args.date_from is None:
        parser.error("--to requires --from")
    asyncio.run(main(cli_args))
