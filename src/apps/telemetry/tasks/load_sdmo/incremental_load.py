"""Инкрементальная догрузка SDMO fc_data — запускается по расписанию.

Читает только НОВЫЕ строки (id > последний загруженный per-station) и COPY'ит их с
живыми индексами. Источник append-only → нет bloat, VACUUM FULL не нужен. Один
процесс, последовательно по станциям (дельта мелкая).
"""

import asyncio
import time

from apps.celery_app import celery_app
from apps.models_registry import *  # noqa: F403
from apps.telemetry.repositories import SdmoFcDataRepository
from apps.telemetry.tasks.load_sdmo import loader
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


class SdmoIncrementalLoad:
    async def run(self, station_ids: list[int] | None = None) -> None:
        async with (
            session_makers["app"]() as app_session,
            session_makers["sdmo"]() as sdmo_session,
        ):
            # Обновить справочники (ловит новые станции / well-связки).
            await loader.load_dimensions(app_session, sdmo_session)

            stations = await loader.list_source_station_ids(
                sdmo_session,
                station_ids,
            )
            cursor = await SdmoFcDataRepository(
                app_session,
            ).get_last_cursor_by_station(stations)

            logger.info("Incremental SDMO load: %s stations", len(stations))
            started = time.monotonic()
            total = 0
            changed_stations: list[int] = []
            for i, station_id in enumerate(stations, 1):
                loaded = await loader.load_station_delta(
                    app_session,
                    sdmo_session,
                    station_id,
                    cursor.get(station_id, (None, 0)),
                    label=f"[{i}/{len(stations)}] ",
                )
                total += loaded
                if loaded:
                    changed_stations.append(station_id)
            elapsed = time.monotonic() - started
            rate = total / elapsed if elapsed else 0
            logger.info(
                "Incremental SDMO load done: +%s rows in %.0fs (%.0f rows/s)",
                f"{total:,}",
                elapsed,
                rate,
            )
            self._dispatch_detectors(changed_stations)

    @staticmethod
    def _dispatch_detectors(changed_stations: list[int]) -> None:
        """Разбудить детекторы по станциям, куда реально приехали строки.

        Запуск через брокер: при standalone-прогоне без Redis загрузка не
        должна падать — детекторы догонит подметальщик по курсорам.
        """
        if not changed_stations:
            return
        try:
            # Ленивый импорт: обходит цикл load_sdmo <-> детекторы на старте.
            from apps.detectors.tasks.dispatch.dispatch import (  # noqa: PLC0415
                dispatch_detectors,
            )

            dispatch_detectors.delay("sdmo", changed_stations)
        except Exception:  # noqa: BLE001
            logger.warning(
                "Detectors dispatch skipped (broker unavailable); "
                "sweep will catch up",
            )


async def main() -> None:
    await SdmoIncrementalLoad().run()


@celery_app.task(name="telemetry.sdmo.incremental_load")
def load_sdmo_incremental() -> None:
    asyncio.run(SdmoIncrementalLoad().run())


if __name__ == "__main__":
    asyncio.run(main())
