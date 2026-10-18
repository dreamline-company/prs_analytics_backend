"""Синхронизация справочника машин «Аргуса» в ``gps_unit``.

    python -m apps.gps.tasks.sync_units.sync_units

Celery: ``gps.units.sync`` — раз в сутки. Полный ``get_units`` (~8 МБ, ~4 с):
карточка машины, последнее сообщение, описания датчиков. Машины, пропавшие из
ответа, помечаются ``removed_at`` и перестают грузиться; вернувшиеся — снова
активны. Какие машины грузить (``tracked``), решает загрузчик треков.
"""

import asyncio
from dataclasses import dataclass

from apps.celery_app import celery_app, run_async
from apps.gps.repositories.gps import GpsUnitRepository
from apps.gps.services.parsing import unit_dto
from apps.gps.tasks.common import (
    UNITS_SYNC_TASK,
    argus_configured,
    build_argus_client,
    local_now,
)
from apps.models_registry import *  # noqa: F403
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.argus.api import ArgusClient

logger = get_logger(__name__)


@dataclass(slots=True)
class UnitsSyncStats:
    units: int = 0
    lifts: int = 0
    removed: int = 0


class SyncGpsUnits:
    def __init__(self, *, client: ArgusClient | None = None) -> None:
        self._client = client

    async def run(self) -> UnitsSyncStats:
        stats = UnitsSyncStats()
        if self._client is None and not argus_configured():
            logger.error("GPS units sync skipped: ARGUS_LOGIN/ARGUS_PASS not set.")
            return stats
        client = self._client or build_argus_client()
        try:
            units = await client.get_units(full=True)
        finally:
            if self._client is None:
                await client.aclose()
        if not units:
            # Пустой ответ — сбой источника, а не «все машины списаны».
            logger.error("GPS units sync: Argus returned no units; nothing changed.")
            return stats

        dtos = [unit_dto(unit) for unit in units]
        async with session_makers["app"]() as session:
            repo = GpsUnitRepository(session)
            await repo.upsert_many(dtos)
            stats.removed = await repo.mark_removed(
                {dto.argus_id for dto in dtos},
                local_now(),
            )
            await session.commit()
        stats.units = len(dtos)
        stats.lifts = sum(dto.is_lift for dto in dtos)
        logger.info(
            "GPS units sync done: units=%s lifts=%s removed=%s.",
            stats.units,
            stats.lifts,
            stats.removed,
        )
        return stats


@celery_app.task(name=UNITS_SYNC_TASK)
def sync_gps_units() -> None:
    run_async(SyncGpsUnits().run())


if __name__ == "__main__":
    asyncio.run(SyncGpsUnits().run())
