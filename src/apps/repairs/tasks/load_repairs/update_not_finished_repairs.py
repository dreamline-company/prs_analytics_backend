"""Актуализация незавершённых ремонтов по ABAI.

Проверяются все идущие ремонты, без ограничения по давности: их десятки, а
ремонт, закрытый в ABAI спустя полгода, иначе висел бы у нас открытым вечно.
Ремонт, которого в ABAI больше нет, помечается ``abai_deleted_at`` и перестаёт
считаться идущим; запись остаётся ради привязанной аналитики, СПО и сводок.
Ремонты, закрытые нами по телеметрии (``closed_by_telemetry_at``), тоже
сверяются: дата окончания из ABAI заменяет нашу, пометка снимается.
"""

import asyncio
from collections.abc import AsyncIterator, Sequence
from datetime import datetime

from sqlalchemy import or_

from apps.models_registry import *  # noqa
from apps.repairs.dto.internal.repositories.repair import UpdateRepairDTO
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.repair import RepairRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIWellWorkoverRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()


class UpdateNotFinishedRepairs:
    ITER_SIZE = 1000

    async def run(self) -> None:
        logger.info("UpdateNotFinishedRepairs task started.")
        now = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        async with session_makers["abai"]() as abai_session:
            abai_repairs_repo = ABAIWellWorkoverRepository(abai_session)
            updated = deleted = 0
            async with session_makers["app"]() as app_session:
                app_repairs_repo = RepairRepository(app_session)
                try:
                    async for nf_repairs in self._iter_nf_repairs(app_repairs_repo):
                        nf_repairs_abai_ids = [r.abai_id for r in nf_repairs]
                        logger.debug(
                            "Batch: %s non-finished repairs fetched from app DB.",
                            len(nf_repairs),
                        )
                        abai_repairs = await abai_repairs_repo.list_by_ids(
                            nf_repairs_abai_ids,
                        )
                        logger.debug(
                            "Matched %s repairs in ABAI for this batch.",
                            len(abai_repairs),
                        )
                        # Пустой ответ на весь батч — скорее сбой ABAI, чем
                        # удаление всех ремонтов разом: ничего не помечаем.
                        if not abai_repairs:
                            continue
                        found_ids = {ar.id for ar in abai_repairs}
                        for abai_id in nf_repairs_abai_ids:
                            if abai_id not in found_ids:
                                logger.info(
                                    "Repair abai_id=%s is gone from ABAI; marked.",
                                    abai_id,
                                )
                                await app_repairs_repo.update_by_abai_id(
                                    abai_id=abai_id,
                                    data=UpdateRepairDTO(abai_deleted_at=now),
                                )
                                deleted += 1
                        for ar in abai_repairs:
                            if ar.dend is not None and isinstance(ar.dend, datetime):
                                logger.debug(
                                    "Updating repair abai_id=%s → end_time=%s.",
                                    ar.id,
                                    ar.dend,
                                )
                                await app_repairs_repo.update_by_abai_id(
                                    abai_id=ar.id,
                                    data=UpdateRepairDTO(
                                        closed_by_telemetry_at=None,
                                        end_time=ar.dend,
                                        work_plan=ar.work_plan,
                                        work_list=ar.work_list,
                                        abai_repair_type_id=ar.repair_type,
                                    ),
                                )
                                updated += 1
                    if updated or deleted:
                        await app_session.commit()

                        logger.info(
                            "Updated %s repairs and set as finished; "
                            "%s marked as deleted in ABAI.",
                            updated,
                            deleted,
                        )
                    logger.info(
                        "UPdating of non finished repairs is done successfully.",
                    )
                except Exception:
                    logger.exception("Update of Not finished repairs is failed.")
                    await app_session.rollback()

    async def _iter_nf_repairs(
        self,
        repairs_repo: RepairRepository,
    ) -> AsyncIterator[Sequence[Repair]]:
        last_r_abai_id = None
        while True:
            filters = []
            if last_r_abai_id is not None:
                filters.append(repairs_repo.model.abai_id > last_r_abai_id)
            nf_repairs = await repairs_repo.get_list(
                spec=QuerySpec(
                    filters=(
                        *filters,
                        or_(
                            repairs_repo.model.is_open,
                            repairs_repo.model.closed_by_telemetry_at.is_not(None),
                        ),
                    ),
                    order_by=(repairs_repo.model.abai_id.asc(),),
                    limit=self.ITER_SIZE,
                ),
            )
            if not nf_repairs:
                break
            yield nf_repairs
            last_r_abai_id = nf_repairs[-1].abai_id
            if len(nf_repairs) < self.ITER_SIZE:
                break


async def main() -> None:
    await UpdateNotFinishedRepairs().run()


if __name__ == "__main__":
    asyncio.run(main())
