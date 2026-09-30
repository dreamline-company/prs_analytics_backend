"""Закрытие ремонтов, когда скважина уже работает, а ABAI их ещё не закрыл.

Признаки смотрятся не раньше чем через сутки после начала ремонта: в первый
день скважина часто ещё работает до прихода бригады. Хватает одного:

- ЦИТС: замеры с дебитом жидкости > 0 минимум в двух разных сутках;
- СДМО: станция на связи (последний отсчёт не старше 2 ч) и за последние
  сутки привод крутился (скорость ротора > 0) не меньше половины отсчётов.

Окончание — первый признак работы: первый такой замер или первый отсчёт с
оборотами. Ремонт помечается ``closed_by_telemetry_at``; когда ABAI закроет
его своей датой, её подставит ``update_not_finished_repairs``.

    python -m apps.repairs.tasks.close_by_telemetry.close_by_telemetry --dry-run
"""

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.repairs.dto.internal.repositories.repair import UpdateRepairDTO
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)

EVIDENCE_DELAY = timedelta(hours=24)
CITS_MIN_DAYS = 2
SDMO_WINDOW = timedelta(hours=24)
SDMO_MAX_STALENESS = timedelta(hours=2)
SDMO_MIN_RUNNING_SHARE = 0.5


def cits_start(measured_at: Sequence[datetime]) -> datetime | None:
    """Первый замер с дебитом, если такие замеры есть в двух разных сутках."""
    if len({moment.date() for moment in measured_at}) < CITS_MIN_DAYS:
        return None
    return min(measured_at)


def sdmo_running(
    total: int,
    running: int,
    last_savetime: datetime | None,
    *,
    now: datetime,
) -> bool:
    """Станция на связи и за окно привод крутился не меньше половины времени."""
    return (
        total > 0
        and last_savetime is not None
        and now - last_savetime <= SDMO_MAX_STALENESS
        and running / total >= SDMO_MIN_RUNNING_SHARE
    )


@dataclass(frozen=True, slots=True)
class Closure:
    repair_id: int
    well_name: str
    end_time: datetime
    source: str  # "ЦИТС" / "СДМО"


class CloseRepairsByTelemetry:
    def __init__(self, session: AsyncSession, *, now: datetime) -> None:
        self.session = session
        self.now = now
        self.repairs = RepairRepository(session)
        self.wells = WellRepository(session)
        self.telemetry = TelemetryRepository(session)
        self.stations = SdmoStationRepository(session)
        self.fc_data = SdmoFcDataRepository(session)

    async def run(self, *, dry_run: bool = False) -> list[Closure]:
        open_repairs = await self.repairs.get_list(
            QuerySpec(
                filters=(
                    Repair.is_open,
                    Repair.start_time <= self.now - EVIDENCE_DELAY,
                ),
                order_by=(Repair.start_time,),
            ),
        )
        wells = {
            well.abai_id: well
            for well in await self.wells.list_by_abai_ids(
                list({repair.abai_well_id for repair in open_repairs}),
            )
        }
        closures = []
        for repair in open_repairs:
            well = wells.get(repair.abai_well_id)
            if well is None:
                continue
            closure = await self._evaluate(repair, well.id, well.name)
            if closure is None:
                continue
            closures.append(closure)
            logger.info(
                "Repair id=%s %s: well works (%s) since %s — %s.",
                repair.id,
                well.name,
                closure.source,
                closure.end_time,
                "dry run" if dry_run else "closed",
            )
            if not dry_run:
                await self.repairs.update_by_id(
                    repair.id,
                    UpdateRepairDTO(
                        end_time=closure.end_time,
                        closed_by_telemetry_at=self.now,
                    ),
                )
        return closures

    async def _evaluate(
        self,
        repair: Repair,
        well_id: int,
        well_name: str,
    ) -> Closure | None:
        since = repair.start_time + EVIDENCE_DELAY
        candidates: list[tuple[datetime, str]] = []

        rows = await self.telemetry.list_by_well_id_in_period(
            well_id,
            date_time_from=since,
        )
        first = cits_start([row.date_time for row in rows if (row.qv_liquid or 0) > 0])
        if first is not None:
            candidates.append((first, "ЦИТС"))

        for station in await self.stations.list_by_well_id(well_id):
            total, running, last = await self.fc_data.get_rotor_running_stats(
                station.id,
                since=max(since, self.now - SDMO_WINDOW),
            )
            if not sdmo_running(total, running, last, now=self.now):
                continue
            started = await self.fc_data.get_first_rotor_running(
                station.id,
                since=since,
            )
            if started is not None:
                candidates.append((started, "СДМО"))

        if not candidates:
            return None
        end_time, source = min(candidates)
        return Closure(repair.id, well_name, end_time, source)


async def main(*, dry_run: bool = False) -> list[Closure]:
    now = datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
    async with session_makers["app"]() as session:
        closures = await CloseRepairsByTelemetry(session, now=now).run(
            dry_run=dry_run,
        )
        if not dry_run:
            await session.commit()
    logger.info(
        "Repairs closed by telemetry: %s%s",
        len(closures),
        " (dry run)" if dry_run else "",
    )
    return closures


@celery_app.task(name="repairs.close_by_telemetry")
def close_repairs_by_telemetry() -> None:
    run_async(main())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--dry-run", action="store_true")
    asyncio.run(main(dry_run=parser.parse_args().dry_run))
