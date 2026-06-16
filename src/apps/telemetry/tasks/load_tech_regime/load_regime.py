import asyncio
from collections.abc import AsyncGenerator, Sequence

from apps.models_registry import *  # noqa
from apps.telemetry.dto.internal.repositories import CreateTechRegimeDTO
from apps.telemetry.repositories import TechRegimeRepository
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import TechModeProdOil
from shared.integrations.abai.repositories import (
    ABAITechModeProdOilRepository,
    ABAIWellRepository,
)
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class ABAILoadTechRegime:
    ITER_BATCH_SIZE = 5000

    async def run(self) -> None:
        async with session_makers["app"]() as app_session:
            app_tech_repo = TechRegimeRepository(app_session)
            app_wells_repo = WellRepository(app_session)
            app_wells_len = len(await app_wells_repo.get_list())

            async with session_makers["abai"]() as abai_session:
                abai_tech_repo = ABAITechModeProdOilRepository(abai_session)
                abai_wells_repo = ABAIWellRepository(abai_session)
                abai_wells_len = len(await abai_wells_repo.get_list())

                if app_wells_len != abai_wells_len:
                    logger.warning(
                        f"Wells not synced. Len of wells in app database: {app_wells_len},"
                        f" len of abai wells: {abai_wells_len}",
                    )

                try:
                    last_tech_regime = await app_tech_repo.get_latest_by_abai_id()
                    last_tech_abai_id = (
                        last_tech_regime.abai_id if last_tech_regime else 0
                    )
                    count = 0
                    async for regimes in self._iter_abai_tech_regimes(
                        abai_tech_repo,
                        last_tech_abai_id,
                    ):
                        count += len(regimes)
                        await app_tech_repo.bulk_create(
                            [
                                CreateTechRegimeDTO(
                                    abai_id=r.id,
                                    abai_well_id=r.well,
                                    start_date=r.dbeg,
                                    end_date=r.dend,
                                    oil=r.oil,
                                    liquid=r.liquid,
                                )
                                for r in regimes
                            ],
                        )

                    await app_session.commit()
                    logger.info("Loaded tech regimes. Loaded items count: %s", regimes)

                except Exception:
                    logger.exception("Error while loading tech regimes")
                    await app_session.rollback()
                    raise

    async def _iter_abai_tech_regimes(
        self,
        abai_tech_repo: ABAITechModeProdOilRepository,
        last_regime_id: int,
    ) -> AsyncGenerator[Sequence[TechModeProdOil]]:
        last_id = last_regime_id
        while True:
            regimes = await abai_tech_repo.get_list(
                spec=QuerySpec(
                    filters=(abai_tech_repo.model.id > last_id,),
                    limit=self.ITER_BATCH_SIZE,
                    order_by=(abai_tech_repo.model.id.asc(),),
                ),
            )
            if not regimes:
                break
            yield regimes
            last_id = regimes[-1].id
            if len(regimes) < self.ITER_BATCH_SIZE:
                break


async def main() -> None:
    await ABAILoadTechRegime().run()


if __name__ == "__main__":
    asyncio.run(main())
