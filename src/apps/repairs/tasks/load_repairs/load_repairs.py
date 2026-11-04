"""Новые ремонты из ABAI (``emg.well_workover``).

1. Keyset по ``id > max(abai_id)`` батчами.
2. Ремонты по скважине, которой нет в ``wells_well`` (``load_wells`` ручной),
   пропускаются: FK на ``wells_well.abai_id`` иначе ронял бы весь батч, а
   курсор стоял бы на месте каждую ночь.
3. Дочитка: ремонты ABAI, начатые за последние ``RESTORE_DAYS``, которых у нас
   нет (скважину завели позже, дыра в ответе источника), — создаются. Шаг 1
   по курсору их уже не увидит.

Ошибка не глотается: ``repairs.sync`` прерывается, шаги по неполным данным
не выполняются.
"""

import asyncio
from collections.abc import AsyncGenerator, Sequence
from datetime import datetime, timedelta

from apps.models_registry import *  # noqa
from apps.repairs.dto.internal.repositories.repair import CreateRepairDTO
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.repositories.well import WellRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellWorkover
from shared.integrations.abai.repositories import ABAIWellWorkoverRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()


def repair_dto(row: WellWorkover) -> CreateRepairDTO:
    return CreateRepairDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        work_list=row.work_list,
        work_plan=row.work_plan,
        repair_type_id=row.repair_work_type,
        abai_repair_type_id=row.repair_type,
        start_time=row.dbeg,
        end_time=row.dend,
    )


class LoadRepairs:
    ITER_SIZE = 5000
    RESTORE_DAYS = 60

    async def run(self, *, now: datetime | None = None) -> None:
        logger.info("LoadRepairs task started.")
        now = now or datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        async with (
            session_makers["app"]() as app_session,
            session_makers["abai"]() as abai_session,
        ):
            app_repairs_repo = RepairRepository(app_session)
            abai_repairs_repo = ABAIWellWorkoverRepository(abai_session)
            known_wells = await WellRepository(app_session).list_abai_ids()
            last_repair = await app_repairs_repo.get_last_by_abai_id()
            logger.debug(
                "Resuming from abai_id=%s.",
                last_repair.abai_id if last_repair else 0,
            )
            try:
                created = skipped = 0
                async for rs in self._iter_abai_repairs(
                    last_repair_id=last_repair.abai_id if last_repair else 0,
                    abai_repair_repo=abai_repairs_repo,
                ):
                    loadable = [r for r in rs if r.well in known_wells]
                    skipped += len(rs) - len(loadable)
                    await app_repairs_repo.bulk_create(
                        data=[repair_dto(r) for r in loadable],
                    )
                    await app_session.commit()
                    created += len(loadable)
                restored = await self._restore_missing(
                    app_repairs_repo,
                    abai_repairs_repo,
                    known_wells,
                    since=now - timedelta(days=self.RESTORE_DAYS),
                )
                await app_session.commit()
            except Exception:
                await app_session.rollback()
                logger.exception("Failed to load repairs.")
                raise
        logger.info(
            "Repairs loaded: created=%s, restored=%s, skipped (unknown well)=%s",
            created,
            restored,
            skipped,
        )

    async def _restore_missing(
        self,
        app_repairs_repo: RepairRepository,
        abai_repairs_repo: ABAIWellWorkoverRepository,
        known_wells: set[int],
        *,
        since: datetime,
    ) -> int:
        source = await abai_repairs_repo.get_list(
            spec=QuerySpec(
                filters=(abai_repairs_repo.model.dbeg >= since,),
                order_by=(abai_repairs_repo.model.id.asc(),),
            ),
        )
        existing = await app_repairs_repo.list_existing_abai_ids(
            [row.id for row in source],
        )
        missing = [
            row for row in source if row.id not in existing and row.well in known_wells
        ]
        if missing:
            logger.warning("Repairs missing locally, restored: %s", len(missing))
            await app_repairs_repo.bulk_create(data=[repair_dto(r) for r in missing])
        return len(missing)

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
