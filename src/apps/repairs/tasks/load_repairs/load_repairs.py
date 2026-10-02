import asyncio
from collections.abc import AsyncGenerator, Sequence

from apps.models_registry import *  # noqa
from apps.repairs.dto.internal.repositories.repair import CreateRepairDTO
from apps.repairs.repositories.repair import RepairRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellWorkover
from shared.integrations.abai.repositories import ABAIWellWorkoverRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class LoadRepairs:
    ITER_SIZE = 5000

    async def run(self) -> None:
        logger.info("LoadRepairs task started.")
        async with session_makers["app"]() as app_session:
            app_repairs_repo = RepairRepository(app_session)

            last_repair = await app_repairs_repo.get_last_by_abai_id()
            logger.debug(
                "Resuming from abai_id=%s.",
                last_repair.abai_id if last_repair else 0,
            )

            async with session_makers["abai"]() as abai_session:
                abai_repairs_repo = ABAIWellWorkoverRepository(abai_session)

                try:
                    c = 0
                    async for rs in self._iter_abai_repairs(
                        last_repair_id=last_repair.abai_id if last_repair else 0,
                        abai_repair_repo=abai_repairs_repo,
                    ):
                        logger.debug(
                            "Batch: fetched %s repairs from ABAI (up to abai_id=%s).",
                            len(rs),
                            rs[-1].id,
                        )
                        c += len(rs)
                        await app_repairs_repo.bulk_create(
                            data=[
                                CreateRepairDTO(
                                    abai_id=r.id,
                                    abai_well_id=r.well,
                                    work_list=r.work_list,
                                    work_plan=r.work_plan,
                                    repair_type_id=r.repair_work_type,
                                    abai_repair_type_id=r.repair_type,
                                    start_time=r.dbeg,
                                    end_time=r.dend,
                                )
                                for r in rs
                            ],
                        )

                        await app_session.commit()
                        logger.debug(
                            "Batch of %s repairs committed (total so far: %s).",
                            len(rs),
                            c,
                        )
                    if c > 0:
                        logger.info("Successfully Loaded %s new repairs.", c)
                    else:
                        logger.info("There is no new repairs. All repairs are synced.")

                except Exception:
                    logger.exception("Failed to load repairs. ")
                    await app_session.rollback()

    @classmethod
    async def _iter_abai_repairs(
        cls,
        last_repair_id: int,
        abai_repair_repo: ABAIWellWorkoverRepository,
    ) -> AsyncGenerator[Sequence[WellWorkover]]:
        repair_id = last_repair_id

        while True:
            rs = await abai_repair_repo.get_list(
                spec=QuerySpec(
                    filters=(abai_repair_repo.model.id > repair_id,),
                    limit=cls.ITER_SIZE,
                    order_by=(abai_repair_repo.model.id.asc(),),
                ),
            )

            if not rs:
                break
            yield rs
            repair_id = rs[-1].id

            if len(rs) < cls.ITER_SIZE:
                break


async def main() -> None:
    await LoadRepairs().run()


if __name__ == "__main__":
    asyncio.run(main())
