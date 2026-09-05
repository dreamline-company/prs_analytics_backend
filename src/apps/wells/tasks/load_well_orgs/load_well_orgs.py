"""Инкрементальная загрузка привязок скважин к орг. объектам (emg.well_org).

Две фазы за прогон:

1. Новые строки — keyset по ``id > max(abai_id)`` батчами.
2. Открытые интервалы — источник закрывает период правкой ``dend`` (или меняет
   организацию) у уже существующей строки, новый id при этом не появляется.
   Поэтому строки с ``dend`` пустым или в будущем перечитываются и
   обновляются, иначе копия навсегда останется с устаревшей привязкой.

Строки без скважины или организации, а также по скважине, которой нет в нашем
``wells_well``, пропускаются (FK на ``wells_well.abai_id``). Организация
хранится как ABAI id без проверки по локальному ``org``: зеркало оргструктуры
пропускает объекты без имени или типа.
"""

import asyncio
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.well_org import (
    CreateWellOrgDTO,
    UpdateWellOrgDTO,
)
from apps.wells.models.well_org import WellOrg
from apps.wells.repositories import WellOrgRepository, WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellOrg as ABAIWellOrg
from shared.integrations.abai.repositories import ABAIWellOrgRepository

logger = get_logger(__name__)


class LoadWellOrgs:
    # Батч keyset-пагинации по источнику.
    ITER_BATCH_SIZE = 5000
    # Размер IN-списка при перечитывании открытых интервалов.
    REFRESH_CHUNK_SIZE = 1000

    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_well_org_repo = ABAIWellOrgRepository(session=abai_session)
        self.well_org_repo = WellOrgRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session
        self.known_well_ids: set[int] = set()

    async def run(self, *, on_date: date | None = None) -> None:
        on_date = on_date or date.today()  # noqa: DTZ011
        self.known_well_ids = await self.well_repo.list_abai_ids()

        try:
            created, skipped = await self._load_new_rows()
            updated = await self._refresh_open_intervals(on_date)
            await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while loading well orgs")
            raise

        logger.info(
            "Well org sync finished: created=%s, updated=%s, skipped=%s",
            created,
            updated,
            skipped,
        )

    async def _load_new_rows(self) -> tuple[int, int]:
        last_abai_id = await self.well_org_repo.get_max_abai_id()
        created = 0
        skipped = 0

        while True:
            rows = await self.abai_well_org_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
            )
            if not rows:
                break

            loadable = [row for row in rows if self._is_loadable(row)]
            skipped += len(rows) - len(loadable)

            await self.well_org_repo.bulk_create(
                [
                    CreateWellOrgDTO(
                        abai_id=row.id,
                        abai_well_id=row.well,
                        abai_org_id=row.org,
                        dbeg=row.dbeg,
                        dend=row.dend,
                    )
                    for row in loadable
                ],
            )
            created += len(loadable)
            last_abai_id = rows[-1].id

            if len(rows) < self.ITER_BATCH_SIZE:
                break

        return created, skipped

    async def _refresh_open_intervals(self, on_date: date) -> int:
        open_rows = await self.well_org_repo.list_open_intervals(on_date)
        if not open_rows:
            return 0

        open_rows_by_abai_id = {row.abai_id: row for row in open_rows}
        abai_ids = sorted(open_rows_by_abai_id)
        updated = 0

        for start in range(0, len(abai_ids), self.REFRESH_CHUNK_SIZE):
            chunk = abai_ids[start : start + self.REFRESH_CHUNK_SIZE]
            source_rows = await self.abai_well_org_repo.list_by_ids(chunk)

            for source_row in source_rows:
                app_row = open_rows_by_abai_id[source_row.id]
                if not self._is_changed(app_row, source_row):
                    continue
                if not self._is_loadable(source_row):
                    logger.warning(
                        "Skip well org %s update: unknown well %s or empty org %s",
                        source_row.id,
                        source_row.well,
                        source_row.org,
                    )
                    continue

                await self.well_org_repo.update_by_abai_id(
                    abai_id=source_row.id,
                    data=UpdateWellOrgDTO(
                        abai_well_id=source_row.well,
                        abai_org_id=source_row.org,
                        dbeg=source_row.dbeg,
                        dend=source_row.dend,
                    ),
                )
                updated += 1

        return updated

    def _is_loadable(self, row: ABAIWellOrg) -> bool:
        return (
            row.well is not None
            and row.org is not None
            and row.well in self.known_well_ids
        )

    @staticmethod
    def _is_changed(app_row: WellOrg, source_row: ABAIWellOrg) -> bool:
        return (
            app_row.abai_well_id != source_row.well
            or app_row.abai_org_id != source_row.org
            or app_row.dbeg != source_row.dbeg
            or app_row.dend != source_row.dend
        )


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadWellOrgs(
            abai_session=abai_session,
            app_session=app_session,
        ).run()


@celery_app.task(name="wells.well_org.incremental_load")
def load_well_orgs_incremental() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
