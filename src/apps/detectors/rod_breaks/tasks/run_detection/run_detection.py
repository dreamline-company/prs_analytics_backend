import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.detector import RodBreakDetector
from apps.detectors.rod_breaks.dto.internal.repositories.detection import (
    CreateRodBreakDetectionDTO,
    CreateRodBreakRunDTO,
    UpdateRodBreakRunDTO,
)
from apps.detectors.rod_breaks.models.detection import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RodBreakRun,
)
from apps.detectors.rod_breaks.repositories.detection import (
    RodBreakDetectionRepository,
    RodBreakRunRepository,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import SdmoStationRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)

# Целевой флот детектора — Danfoss VLT (ЭВН-КУДУ).
TARGET_TYPE_1900 = 6


class RunRodBreakDetection:
    """Ежедневный прогон R2 по всему флоту type_1900=6.

    Создаёт RodBreakRun, гоняет детектор по каждой скважине и построчно пишет
    RodBreakDetection. Транзакциями управляет таска (репозитории не коммитят).
    """

    async def run(self, as_of: datetime | None = None) -> None:
        as_of = as_of or datetime.now(UTC)
        started_at = as_of.replace(tzinfo=None)

        async with session_makers["app"]() as session:
            run_repo = RodBreakRunRepository(session)
            self._session = session
            self._detection_repo = RodBreakDetectionRepository(session)
            self._detector = RodBreakDetector(session)

            run = await run_repo.create(
                CreateRodBreakRunDTO(
                    as_of_date=as_of.date(),
                    started_at=started_at,
                    config_version=config.CONFIG_VERSION,
                    status=RUN_STATUS_RUNNING,
                ),
            )
            run_id = run.id
            await session.commit()

            well_ids = await self._target_well_ids(session)
            logger.info("Rod-break run %s: %s wells", run_id, len(well_ids))

            try:
                fired_count = await self._scan_wells(
                    run_id=run_id,
                    well_ids=well_ids,
                    as_of=as_of,
                )
                await run_repo.update(
                    UpdateRodBreakRunDTO(
                        finished_at=datetime.now(UTC).replace(tzinfo=None),
                        wells_scanned=len(well_ids),
                        detections_count=fired_count,
                        status=RUN_STATUS_COMPLETED,
                    ),
                    filters=(RodBreakRun.id == run_id,),
                )
                await session.commit()
                logger.info(
                    "Rod-break run %s done: %s fired of %s",
                    run_id,
                    fired_count,
                    len(well_ids),
                )
            except Exception:
                logger.exception("Rod-break run %s failed", run_id)
                await session.rollback()
                await run_repo.update(
                    UpdateRodBreakRunDTO(status=RUN_STATUS_FAILED),
                    filters=(RodBreakRun.id == run_id,),
                )
                await session.commit()
                raise

    async def _scan_wells(
        self,
        *,
        run_id: int,
        well_ids: list[int],
        as_of: datetime,
    ) -> int:
        fired_count = 0
        for well_id in well_ids:
            result = await self._detector.run_for_well(well_id, as_of)
            await self._detection_repo.create(
                CreateRodBreakDetectionDTO(
                    run_id=run_id,
                    well_id=result.well_id,
                    station_sdmo_id=result.station_sdmo_id,
                    fired=result.fired,
                    fired_at=result.fired_at,
                    failure_dt=result.failure_dt,
                    lead_time_hours=result.lead_time_hours,
                    event_class=result.event_class,
                    base_moment=result.base_moment,
                    low_confidence=result.low_confidence,
                    evidence=result.evidence,
                ),
            )
            fired_count += int(result.fired)
            await self._session.commit()
        return fired_count

    @staticmethod
    async def _target_well_ids(session: AsyncSession) -> list[int]:
        stations = await SdmoStationRepository(session).get_list(
            QuerySpec(
                filters=(
                    SdmoStation.type_1900 == TARGET_TYPE_1900,
                    SdmoStation.well_id.is_not(None),
                ),
                order_by=(SdmoStation.well_id,),
            ),
        )
        # Дедуплицируем: у скважины может быть несколько станций.
        seen: dict[int, None] = {}
        for station in stations:
            if station.well_id is not None:
                seen.setdefault(station.well_id, None)
        return list(seen)


async def main() -> None:
    await RunRodBreakDetection().run()


if __name__ == "__main__":
    asyncio.run(main())
