import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime

from apps.models_registry import *  # noqa
from apps.org.repositories import NGDURepository
from apps.telemetry.dto.internal.repositories import CreateTelemetryDTO
from apps.telemetry.repositories import TelemetryRepository
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.wincc.models import NGDUWinccTelemetryModel
from shared.integrations.wincc.repositories import (
    DMGWinccTelemetryRepository,
    KainarWinccTelemetryRepository,
    NGDUWinccTelemetryRepository,
)
from shared.repository.sqlalchemy import QuerySpec
from utils.wells import make_code

logger = get_logger(__name__)


class WinccLoadTelemetry:
    ITER_BATCH_SIZE = 50_000

    async def run(self) -> None:
        async with session_makers["app"]() as app_session:
            app_wells_repo = WellRepository(app_session)
            app_wells = await app_wells_repo.get_list()
            app_wells_ids = {well.name: well.id for well in app_wells}
            del app_wells

            ngdu_repo = NGDURepository(app_session)
            all_ngdu = await ngdu_repo.get_list()

            ngdu_name_to_id_dict: dict[str, int] = {n.name: n.id for n in all_ngdu}
        await self._load_kainar(ngdu_name_to_id_dict, app_wells_ids)
        await self._load_dmg(ngdu_name_to_id_dict, app_wells_ids)

    async def _load_dmg(
        self,
        ngdu_name_to_id_dict: dict[str, int],
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading DMG...")
        async with session_makers["dmg_telemetry"]() as dmg_session:
            dmg_ngdu_name = "доссормунайгаз"
            for k, v in ngdu_name_to_id_dict.items():
                if dmg_ngdu_name in k.lower():
                    dmg_ngdu_id = v
                    dmg_tm_repo = DMGWinccTelemetryRepository(dmg_session)
                    await self._load_ngdu(
                        ngdu_id=dmg_ngdu_id,
                        app_wells_ids=app_wells_ids,
                        ngdu_tm_repo=dmg_tm_repo,
                    )

    async def _load_kainar(
        self,
        ngdu_name_to_id_dict: dict[str, int],
        app_wells_ids: dict[str, int],
    ) -> None:
        logger.info("Loading Kainar...")
        async with session_makers["kainar_telemetry"]() as kainar_session:
            kainar_ngdu_name = "кайнармунайгаз"
            for k, v in ngdu_name_to_id_dict.items():
                if kainar_ngdu_name in k.lower():
                    kainar_ngdu_id = v
                    kainar_tm_repo = KainarWinccTelemetryRepository(kainar_session)
                    await self._load_ngdu(
                        ngdu_id=kainar_ngdu_id,
                        app_wells_ids=app_wells_ids,
                        ngdu_tm_repo=kainar_tm_repo,
                    )

    async def _load_ngdu(
        self,
        ngdu_id: int,
        ngdu_tm_repo: NGDUWinccTelemetryRepository,
        app_wells_ids: dict[str, int],
    ) -> None:

        async with session_makers["app"]() as app_session:
            telemetry_repo = TelemetryRepository(app_session)
            last_tm = await telemetry_repo.get_last_by_ngdu_id(
                ngdu_id=ngdu_id,
            )
            try:
                c = 0
                n = 0
                async for tms in self._iter_tm(
                    ngdu_tm_repo,
                    last_tm_time=last_tm.date_time if last_tm else None,
                ):
                    c += 1
                    n += len(tms)
                    is_saved = await self._save_tm(
                        tms,
                        app_wells_ids,
                        ngdu_id,
                        telemetry_repo,
                    )
                    if is_saved:
                        await app_session.commit()
                    logger.debug(ngdu_id, n, c, is_saved)
            except Exception:
                logger.exception("Error while loading NGDU #%s telemetry.", ngdu_id)
                await app_session.rollback()
            else:
                logger.info("NGDU #%s telemetry loaded successfully.", ngdu_id)

    @classmethod
    async def _save_tm(
        cls,
        tms: Sequence[NGDUWinccTelemetryModel],
        app_wells_ids: dict[str, int],
        dmg_ngdu_id: int,
        app_telemetry_repo: TelemetryRepository,
    ) -> bool:
        bulk_data = []
        for tm in tms:
            if not tm.Meas_date or not tm.Well or not tm.Oil_field:
                logger.warning(
                    "TM skipped. Oilfield: %s, well: %s",
                    tm.Oil_field,
                    tm.Well,
                )
                continue
            try:
                well_name = make_code(tm.Oil_field, tm.Well)
            except ValueError:
                logger.exception(
                    "TM skipped. Could form well name %s %s. %s",
                    tm.Oil_field,
                    tm.Well,
                )
                continue
            well_id = app_wells_ids.get(well_name)
            if not well_id:
                logger.error(
                    "Well %s not found in app database.",
                    well_name,
                )
                continue
            bulk_data.append(
                CreateTelemetryDTO(
                    well_id=well_id,
                    date_time=tm.Meas_date,
                    qv_liquid=tm.Qv_liq,
                    qm_oil=tm.Qm_oil,
                    ngdu_id=dmg_ngdu_id,
                    oil_field=tm.Oil_field,
                ),
            )
        if bulk_data:
            logger.debug("Bulking: ", len(bulk_data))
            await app_telemetry_repo.bulk_create(data=bulk_data)
            return True
        return False

    async def _iter_tm(
        self,
        wincc_tm_repo: NGDUWinccTelemetryRepository,
        last_tm_time: datetime | None,
    ) -> AsyncGenerator[Sequence[NGDUWinccTelemetryModel]]:
        last_time = last_tm_time
        while True:
            filters = []
            if last_time:
                filters = [wincc_tm_repo.model.Meas_date > last_time]
            tms = await wincc_tm_repo.get_list(
                spec=QuerySpec(
                    filters=(*filters,),
                    limit=self.ITER_BATCH_SIZE,
                    order_by=(wincc_tm_repo.model.Meas_date.asc(),),
                ),
            )
            if not tms:
                break
            logger.debug("Selected tms: ", len(tms))
            yield tms
            last_time = tms[-1].Meas_date
            if len(tms) < self.ITER_BATCH_SIZE:
                break


async def main() -> None:
    await WinccLoadTelemetry().run()


if __name__ == "__main__":
    asyncio.run(main())
