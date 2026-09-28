"""Инкрементальная загрузка статусов скважин из ABAI (emg.well_status).

За прогон:

1. Справочники статусов и причин — целиком (десятки и ~1 тыс. строк): новые
   создаются, изменившиеся обновляются.
2. Новые интервалы — keyset по ``id > max(abai_id)`` батчами.
3. Открытые интервалы (``dend`` в будущем) — источник закрывает интервал
   правкой ``dend`` у существующей строки, новый id при этом не появляется.
   Такие строки перечитываются и обновляются; исчезнувшие в источнике
   удаляются: ошибочно заведённый и удалённый простой иначе навсегда исключил
   бы скважину из мониторинга.

Время копируется как есть — UTC, открытый интервал до 3333-12-31. Строки по
скважине, которой нет в нашем ``wells_well``, пропускаются (FK на
``wells_well.abai_id``).
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.well_status import (
    CreateWellStatusDTO,
    CreateWellStatusReasonDTO,
    CreateWellStatusTypeDTO,
    UpdateWellStatusDTO,
    UpdateWellStatusReasonDTO,
    UpdateWellStatusTypeDTO,
)
from apps.wells.models.well_status import (
    WellStatus,
    WellStatusReason,
    WellStatusType,
)
from apps.wells.repositories import (
    WellRepository,
    WellStatusReasonRepository,
    WellStatusRepository,
    WellStatusTypeRepository,
)
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import Reason as ABAIReason
from shared.integrations.abai.models import WellStatus as ABAIWellStatus
from shared.integrations.abai.models import WellStatusType as ABAIWellStatusType
from shared.integrations.abai.repositories import (
    ABAIReasonRepository,
    ABAIWellStatusRepository,
    ABAIWellStatusTypeRepository,
)

logger = get_logger(__name__)


class LoadWellStatus:
    # Батч keyset-пагинации по источнику.
    ITER_BATCH_SIZE = 5000
    # Размер IN-списка при перечитывании открытых интервалов.
    REFRESH_CHUNK_SIZE = 1000

    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_status_repo = ABAIWellStatusRepository(session=abai_session)
        self.abai_type_repo = ABAIWellStatusTypeRepository(session=abai_session)
        self.abai_reason_repo = ABAIReasonRepository(session=abai_session)
        self.status_repo = WellStatusRepository(session=app_session)
        self.type_repo = WellStatusTypeRepository(session=app_session)
        self.reason_repo = WellStatusReasonRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session
        self.known_well_ids: set[int] = set()
        self.known_type_ids: set[int] = set()
        self.known_reason_ids: set[int] = set()

    async def run(self, *, now: datetime | None = None) -> None:
        now = now or datetime.now(UTC).replace(tzinfo=None)
        self.known_well_ids = await self.well_repo.list_abai_ids()

        try:
            await self._sync_types()
            await self._sync_reasons()
            created, skipped = await self._load_new_rows()
            updated, deleted = await self._refresh_open_intervals(now)
            await self.app_session.commit()
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while loading well statuses")
            raise

        logger.info(
            "Well status sync finished: created=%s, updated=%s, deleted=%s, skipped=%s",
            created,
            updated,
            deleted,
            skipped,
        )

    async def _sync_types(self) -> None:
        source = await self.abai_type_repo.list_all()
        local = {row.abai_id: row for row in await self.type_repo.list_all()}

        await self.type_repo.bulk_create(
            [
                CreateWellStatusTypeDTO(
                    abai_id=row.id,
                    name_ru=row.name_ru,
                    code=row.code,
                    name_short_ru=row.name_short_ru,
                    tbd_id=row.tbd_id,
                )
                for row in source
                if row.id not in local
            ],
        )
        for row in source:
            app_row = local.get(row.id)
            if app_row is not None and self._type_changed(app_row, row):
                await self.type_repo.update_by_abai_id(
                    abai_id=row.id,
                    data=UpdateWellStatusTypeDTO(
                        name_ru=row.name_ru,
                        code=row.code,
                        name_short_ru=row.name_short_ru,
                        tbd_id=row.tbd_id,
                    ),
                )
        self.known_type_ids = {row.id for row in source} | set(local)

    async def _sync_reasons(self) -> None:
        source = await self.abai_reason_repo.list_all()
        local = {row.abai_id: row for row in await self.reason_repo.list_all()}

        await self.reason_repo.bulk_create(
            [
                CreateWellStatusReasonDTO(
                    abai_id=row.id,
                    reason_type=row.reason_type,
                    name_ru=row.name_ru,
                    code=row.code,
                    parent=row.parent,
                    name_short_ru=row.name_short_ru,
                )
                for row in source
                if row.id not in local
            ],
        )
        for row in source:
            app_row = local.get(row.id)
            if app_row is not None and self._reason_changed(app_row, row):
                await self.reason_repo.update_by_abai_id(
                    abai_id=row.id,
                    data=UpdateWellStatusReasonDTO(
                        reason_type=row.reason_type,
                        name_ru=row.name_ru,
                        code=row.code,
                        parent=row.parent,
                        name_short_ru=row.name_short_ru,
                    ),
                )
        self.known_reason_ids = {row.id for row in source} | set(local)

    async def _load_new_rows(self) -> tuple[int, int]:
        last_abai_id = await self.status_repo.get_max_abai_id()
        created = 0
        skipped = 0

        while True:
            rows = await self.abai_status_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
            )
            if not rows:
                break

            loadable = [row for row in rows if self._is_loadable(row)]
            skipped += len(rows) - len(loadable)

            await self.status_repo.bulk_create(
                [self._create_dto(row) for row in loadable],
            )
            created += len(loadable)
            last_abai_id = rows[-1].id

            if len(rows) < self.ITER_BATCH_SIZE:
                break

        return created, skipped

    async def _refresh_open_intervals(self, now: datetime) -> tuple[int, int]:
        open_rows = await self.status_repo.list_open_intervals(now)
        if not open_rows:
            return 0, 0

        open_rows_by_abai_id = {row.abai_id: row for row in open_rows}
        abai_ids = sorted(open_rows_by_abai_id)
        updated = 0
        gone: list[int] = []

        for start in range(0, len(abai_ids), self.REFRESH_CHUNK_SIZE):
            chunk = abai_ids[start : start + self.REFRESH_CHUNK_SIZE]
            source_rows = await self.abai_status_repo.list_by_ids(chunk)
            found = {row.id for row in source_rows}
            gone.extend(abai_id for abai_id in chunk if abai_id not in found)

            for source_row in source_rows:
                app_row = open_rows_by_abai_id[source_row.id]
                if not self._is_changed(app_row, source_row):
                    continue
                if not self._is_loadable(source_row):
                    logger.warning(
                        "Skip well status %s update: unknown well %s or status %s",
                        source_row.id,
                        source_row.well,
                        source_row.status,
                    )
                    continue

                await self.status_repo.update_by_abai_id(
                    abai_id=source_row.id,
                    data=UpdateWellStatusDTO(
                        abai_well_id=source_row.well,
                        status=source_row.status,
                        reason=self._reason_or_none(source_row),
                        dbeg=source_row.dbeg,
                        dend=source_row.dend,
                    ),
                )
                updated += 1

        if gone:
            logger.info("Well statuses deleted in source: %s", len(gone))
            await self.status_repo.delete_by_abai_ids(gone)
        return updated, len(gone)

    def _create_dto(self, row: ABAIWellStatus) -> CreateWellStatusDTO:
        return CreateWellStatusDTO(
            abai_id=row.id,
            abai_well_id=row.well,
            status=row.status,
            reason=self._reason_or_none(row),
            dbeg=row.dbeg,
            dend=row.dend,
        )

    def _is_loadable(self, row: ABAIWellStatus) -> bool:
        return row.well in self.known_well_ids and row.status in self.known_type_ids

    def _reason_or_none(self, row: ABAIWellStatus) -> int | None:
        return row.reason if row.reason in self.known_reason_ids else None

    def _is_changed(self, app_row: WellStatus, source_row: ABAIWellStatus) -> bool:
        return (
            app_row.abai_well_id != source_row.well
            or app_row.status != source_row.status
            or app_row.reason != self._reason_or_none(source_row)
            or app_row.dbeg != source_row.dbeg
            or app_row.dend != source_row.dend
        )

    @staticmethod
    def _type_changed(app_row: WellStatusType, row: ABAIWellStatusType) -> bool:
        return (
            app_row.name_ru != row.name_ru
            or app_row.code != row.code
            or app_row.name_short_ru != row.name_short_ru
            or app_row.tbd_id != row.tbd_id
        )

    @staticmethod
    def _reason_changed(app_row: WellStatusReason, row: ABAIReason) -> bool:
        return (
            app_row.reason_type != row.reason_type
            or app_row.name_ru != row.name_ru
            or app_row.code != row.code
            or app_row.parent != row.parent
            or app_row.name_short_ru != row.name_short_ru
        )


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadWellStatus(
            abai_session=abai_session,
            app_session=app_session,
        ).run()


@celery_app.task(name="wells.well_status.incremental_load")
def load_well_status_incremental() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
