"""Инкрементальная загрузка периодов эксплуатации скважин (emg.well_expl).

Две фазы за прогон:

1. Новые строки — keyset по ``id > max(abai_id)`` батчами.
2. Открытые интервалы — источник закрывает период правкой ``dend`` у уже
   существующей строки (было ``3333-12-31`` → стало дата смены способа), новый
   id при этом не появляется. Поэтому строки с ``dend`` пустым или в будущем
   перечитываются и обновляются, иначе копия навсегда останется с открытым
   периодом.

Строки без скважины или по скважине, которой нет в нашем ``wells_well``,
пропускаются (FK на ``wells_well.abai_id``). Неизвестный способ эксплуатации
подтягивается из справочника-источника на лету.
"""

import asyncio
from collections.abc import Sequence
from datetime import date

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.well_expl import (
    CreateWellExplDTO,
    CreateWellExplTypeDTO,
    UpdateWellExplDTO,
)
from apps.wells.models.well_expl import WellExpl
from apps.wells.repositories import (
    WellExplRepository,
    WellExplTypeRepository,
    WellRepository,
)
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import WellExpl as ABAIWellExpl
from shared.integrations.abai.repositories import (
    ABAIWellExplRepository,
    ABAIWellExplTypeRepository,
)

logger = get_logger(__name__)


class LoadWellExpl:
    # Батч keyset-пагинации по источнику.
    ITER_BATCH_SIZE = 5000
    # Размер IN-списка при перечитывании открытых интервалов.
    REFRESH_CHUNK_SIZE = 1000

    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_expl_repo = ABAIWellExplRepository(session=abai_session)
        self.abai_type_repo = ABAIWellExplTypeRepository(session=abai_session)
        self.expl_repo = WellExplRepository(session=app_session)
        self.type_repo = WellExplTypeRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session
        self.known_well_ids: set[int] = set()
        self.known_type_ids: set[int] = set()

    async def run(self, *, on_date: date | None = None) -> None:
        on_date = on_date or date.today()  # noqa: DTZ011
        self.known_well_ids = await self.well_repo.list_abai_ids()
        self.known_type_ids = await self.type_repo.list_abai_ids()

        try:
            created, skipped = await self._load_new_rows()
            updated = await self._refresh_open_intervals(on_date)
            await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while loading well expl")
            raise

        logger.info(
            "Well expl sync finished: created=%s, updated=%s, skipped=%s",
            created,
            updated,
            skipped,
        )

    async def _load_new_rows(self) -> tuple[int, int]:
        last_abai_id = await self.expl_repo.get_max_abai_id()
        created = 0
        skipped = 0

        while True:
            rows = await self.abai_expl_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
            )
            if not rows:
                break

            loadable = [row for row in rows if self._is_loadable(row)]
            skipped += len(rows) - len(loadable)
            await self._ensure_types(loadable)

            await self.expl_repo.bulk_create(
                [
                    CreateWellExplDTO(
                        abai_id=row.id,
                        abai_well_id=row.well,
                        expl=self._expl_or_none(row),
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
        open_rows = await self.expl_repo.list_open_intervals(on_date)
        if not open_rows:
            return 0

        open_rows_by_abai_id = {row.abai_id: row for row in open_rows}
        abai_ids = sorted(open_rows_by_abai_id)
        updated = 0

        for start in range(0, len(abai_ids), self.REFRESH_CHUNK_SIZE):
            chunk = abai_ids[start : start + self.REFRESH_CHUNK_SIZE]
            source_rows = await self.abai_expl_repo.list_by_ids(chunk)
            await self._ensure_types(
                [row for row in source_rows if self._is_loadable(row)],
            )

            for source_row in source_rows:
                app_row = open_rows_by_abai_id[source_row.id]
                if not self._is_changed(app_row, source_row):
                    continue
                if not self._is_loadable(source_row):
                    logger.warning(
                        "Skip well expl %s update: unknown well %s",
                        source_row.id,
                        source_row.well,
                    )
                    continue

                await self.expl_repo.update_by_abai_id(
                    abai_id=source_row.id,
                    data=UpdateWellExplDTO(
                        abai_well_id=source_row.well,
                        expl=self._expl_or_none(source_row),
                        dbeg=source_row.dbeg,
                        dend=source_row.dend,
                    ),
                )
                updated += 1

        return updated

    async def _ensure_types(self, rows: Sequence[ABAIWellExpl]) -> None:
        """Догрузить в справочник способы эксплуатации, которых у нас ещё нет."""
        missing_ids = {
            row.expl
            for row in rows
            if row.expl is not None and row.expl not in self.known_type_ids
        }
        if not missing_ids:
            return

        source_types = await self.abai_type_repo.list_by_ids(sorted(missing_ids))
        if source_types:
            await self.type_repo.bulk_create(
                [
                    CreateWellExplTypeDTO(
                        abai_id=t.id,
                        name_ru=t.name_ru,
                        name_short_ru=t.name_short_ru,
                        tbd_id=t.tbd_id,
                        code=t.code,
                    )
                    for t in source_types
                ],
            )
            self.known_type_ids |= {t.id for t in source_types}
            logger.info(
                "Well expl types added on the fly: %s",
                sorted(t.id for t in source_types),
            )

        unresolved = missing_ids - self.known_type_ids
        if unresolved:
            logger.warning(
                "Unknown well expl types (stored as NULL): %s",
                sorted(unresolved),
            )

    def _is_loadable(self, row: ABAIWellExpl) -> bool:
        return row.well is not None and row.well in self.known_well_ids

    def _expl_or_none(self, row: ABAIWellExpl) -> int | None:
        return row.expl if row.expl in self.known_type_ids else None

    def _is_changed(self, app_row: WellExpl, source_row: ABAIWellExpl) -> bool:
        return (
            app_row.abai_well_id != source_row.well
            or app_row.expl != self._expl_or_none(source_row)
            or app_row.dbeg != source_row.dbeg
            or app_row.dend != source_row.dend
        )


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadWellExpl(
            abai_session=abai_session,
            app_session=app_session,
        ).run()


@celery_app.task(name="wells.well_expl.incremental_load")
def load_well_expl_incremental() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
