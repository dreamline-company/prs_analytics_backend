"""Инкрементальная догрузка SDMO fc_data одного НГДУ — запускается по расписанию.

Читает только НОВЫЕ строки (``(savetime, id)`` > курсор станции) и COPY'ит их
с живыми индексами. Источник append-only → нет bloat, VACUUM FULL не нужен.
Один процесс, последовательно по станциям (дельта мелкая). На каждый НГДУ с
настроенной базой beat ставит отдельную таску: недоступный источник не
задерживает остальные. Наложение двух прогонов одного НГДУ снимает замок.

Запуск вручную:
    python -m apps.telemetry.tasks.load_sdmo.incremental_load --ngdu 12
"""

import argparse
import asyncio
import time

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.telemetry.repositories import SdmoFcDataRepository
from apps.telemetry.tasks.load_sdmo import loader
from apps.telemetry.tasks.load_sdmo.lock import ngdu_load_lock
from apps.telemetry.tasks.load_sdmo.sources import as_ngdu, sdmo_session_maker
from core import get_logger
from shared.constants.ngdu import AbaiNGDUIDsEnum
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)


class SdmoIncrementalLoad:
    async def run(
        self,
        abai_ngdu_id: int,
        station_ids: list[int] | None = None,
    ) -> None:
        """``station_ids`` — натуральные ``sdmo_id`` станций внутри НГДУ
        (по умолчанию все)."""
        ngdu = as_ngdu(abai_ngdu_id)
        async with ngdu_load_lock(int(ngdu)) as acquired:
            if not acquired:
                logger.warning(
                    "Incremental SDMO load [%s] skipped: previous run still going",
                    ngdu.name,
                )
                return
            await self._run(ngdu, station_ids)

    async def _run(
        self,
        ngdu: AbaiNGDUIDsEnum,
        station_ids: list[int] | None,
    ) -> None:
        async with (
            session_makers["app"]() as app_session,
            sdmo_session_maker(ngdu)() as sdmo_session,
        ):
            # Обновить справочники (ловит новые станции / well-связки).
            stations = await loader.load_dimensions(app_session, sdmo_session, ngdu)
            stations = loader.select_stations(stations, station_ids)
            cursor = await SdmoFcDataRepository(
                app_session,
            ).get_last_cursor_by_station([station.id for station in stations])

            logger.info(
                "Incremental SDMO load [%s]: %s stations",
                ngdu.name,
                len(stations),
            )
            started = time.monotonic()
            total = 0
            changed_stations: list[int] = []
            for i, station in enumerate(stations, 1):
                loaded = await loader.load_station_delta(
                    app_session,
                    sdmo_session,
                    station,
                    cursor.get(station.id, (None, 0)),
                    label=f"[{ngdu.name} {i}/{len(stations)}] ",
                )
                total += loaded
                if loaded:
                    changed_stations.append(station.id)
            elapsed = time.monotonic() - started
            rate = total / elapsed if elapsed else 0
            logger.info(
                "Incremental SDMO load [%s] done: +%s rows in %.0fs (%.0f rows/s)",
                ngdu.name,
                f"{total:,}",
                elapsed,
                rate,
            )
            self._dispatch_detectors(changed_stations)

    @staticmethod
    def _dispatch_detectors(changed_stations: list[int]) -> None:
        """Разбудить детекторы по станциям (локальные id), куда приехали строки.

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
                "Detectors dispatch skipped (broker unavailable); sweep will catch up",
            )


async def main(abai_ngdu_id: int) -> None:
    await SdmoIncrementalLoad().run(abai_ngdu_id)


@celery_app.task(name="telemetry.sdmo.incremental_load")
def load_sdmo_incremental(abai_ngdu_id: int) -> None:
    run_async(SdmoIncrementalLoad().run(abai_ngdu_id))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SDMO fc_data incremental load")
    parser.add_argument(
        "--ngdu",
        type=int,
        required=True,
        choices=[int(ngdu) for ngdu in AbaiNGDUIDsEnum],
        help="ABAI id НГДУ (AbaiNGDUIDsEnum)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    asyncio.run(main(_parse_args().ngdu))
