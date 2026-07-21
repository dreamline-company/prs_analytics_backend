import asyncio
from collections.abc import AsyncGenerator, Sequence

from apps.models_registry import *  # noqa: F403
from apps.telemetry.dto.internal.repositories import (
    CreateSdmoFcDataDTO,
    CreateSdmoFcRegDTO,
    CreateSdmoStationDTO,
)
from apps.telemetry.repositories import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.sdmo.models import FcDataDayParted
from shared.integrations.sdmo.repositories import (
    SDMOFcDataDayPartedRepository,
    SDMOFcRegRepository,
    SDMOStationRepository,
)
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class SdmoLoadTelemetry:
    ITER_BATCH_SIZE = 5000

    async def run(self) -> None:
        async with session_makers["app"]() as app_session:
            wells_repo = WellRepository(app_session)
            wells = await wells_repo.get_list()
            wells_ids = {well.name: well.id for well in wells}
            del wells

        async with session_makers["sdmo"]() as sdmo_session:
            sdmo_reg_repo = SDMOFcRegRepository(sdmo_session)
            sdmo_station_repo = SDMOStationRepository(sdmo_session)
            sdmo_fc_data_repo = SDMOFcDataDayPartedRepository(sdmo_session)

            await self._load_fc_reg(sdmo_reg_repo)
            await self._load_stations(sdmo_station_repo, wells_ids)
            await self._load_fc_data(sdmo_fc_data_repo)

    async def _load_fc_reg(
        self,
        sdmo_reg_repo: SDMOFcRegRepository,
    ) -> None:
        logger.info("Loading SDMO fc_reg...")
        regs = await sdmo_reg_repo.get_list()

        async with session_makers["app"]() as app_session:
            app_reg_repo = SdmoFcRegRepository(app_session)
            try:
                await app_reg_repo.delete_all()
                await app_reg_repo.bulk_create(
                    [
                        CreateSdmoFcRegDTO(
                            sdmo_id=reg.id,
                            type_1900=reg.type_1900,
                            addr=reg.addr,
                            name=reg.name,
                            units=reg.units,
                            koef=reg.koef,
                            type=reg.type,
                            dynamic=reg.dynamic,
                            info=reg.info,
                            lora_bytes_size=reg.lora_bytes_size,
                        )
                        for reg in regs
                    ],
                )
                await app_session.commit()
                logger.info("Loaded SDMO fc_reg. Count: %s", len(regs))
            except Exception:
                logger.exception("Error while loading SDMO fc_reg")
                await app_session.rollback()
                raise

    async def _load_stations(
        self,
        sdmo_station_repo: SDMOStationRepository,
        wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading SDMO stations...")
        stations = await sdmo_station_repo.get_list()

        async with session_makers["app"]() as app_session:
            app_station_repo = SdmoStationRepository(app_session)
            try:
                await app_station_repo.delete_all()
                await app_station_repo.bulk_create(
                    [
                        CreateSdmoStationDTO(
                            sdmo_id=station.id,
                            place_id=station.place_id,
                            name=station.name,
                            code=station.code,
                            type_1900=station.type_1900,
                            serial_number=station.serial_number,
                            active=station.active,
                            status=station.status,
                            well_id=self._resolve_well_id(station.code, wells_ids),
                        )
                        for station in stations
                    ],
                )
                await app_session.commit()
                logger.info("Loaded SDMO stations. Count: %s", len(stations))
            except Exception:
                logger.exception("Error while loading SDMO stations")
                await app_session.rollback()
                raise

    async def _load_fc_data(
        self,
        sdmo_fc_data_repo: SDMOFcDataDayPartedRepository,
    ) -> None:
        logger.info("Loading SDMO fc_data...")
        async with session_makers["app"]() as app_session:
            app_fc_data_repo = SdmoFcDataRepository(app_session)
            last_id = await app_fc_data_repo.get_last_sdmo_id()
            try:
                total = 0
                async for rows in self._iter_fc_data(sdmo_fc_data_repo, last_id):
                    await app_fc_data_repo.bulk_create(
                        [
                            CreateSdmoFcDataDTO(
                                sdmo_id=row.id,
                                sdmo_station_id=row.station_id,
                                day=row.day,
                                savetime=row.savetime,
                                data=row.data,
                            )
                            for row in rows
                        ],
                    )
                    await app_session.commit()
                    total += len(rows)
                    logger.debug(
                        "SDMO fc_data batch: %s, total: %s",
                        len(rows),
                        total,
                    )
                logger.info("Loaded SDMO fc_data. Count: %s", total)
            except Exception:
                logger.exception("Error while loading SDMO fc_data")
                await app_session.rollback()
                raise

    @staticmethod
    def _resolve_well_id(
        code: str | None,
        wells_ids: dict[str, int],
    ) -> int | None:
        if not code:
            return None
        return wells_ids.get(code.strip())

    async def _iter_fc_data(
        self,
        sdmo_fc_data_repo: SDMOFcDataDayPartedRepository,
        last_id: int,
    ) -> AsyncGenerator[Sequence[FcDataDayParted]]:
        while True:
            rows = await sdmo_fc_data_repo.get_list(
                spec=QuerySpec(
                    filters=(FcDataDayParted.id > last_id,),
                    order_by=(FcDataDayParted.id.asc(),),
                    limit=self.ITER_BATCH_SIZE,
                ),
            )
            if not rows:
                break
            yield rows
            last_id = rows[-1].id
            if len(rows) < self.ITER_BATCH_SIZE:
                break


async def main() -> None:
    await SdmoLoadTelemetry().run()


if __name__ == "__main__":
    asyncio.run(main())
