import asyncio

from sqlalchemy.ext.asyncio import AsyncSession

from apps.wells.dto.internal.repositories.well import CreateWellDTO
from apps.wells.repositories import WellRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories.wells import ABAIWellRepository
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class LoadWells:
    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_wells_repo = ABAIWellRepository(session=abai_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session

    async def run(self) -> None:
        abai_wells = await self.abai_wells_repo.get_list(spec=QuerySpec())
        abai_wells = [w for w in abai_wells if len(w.uwi) == 8 and w.uwi[4:].isdigit()]
        app_wells = await self.well_repo.get_list()

        abai_wells_with_name_by_id = {
            well.id: well for well in abai_wells if well.uwi is not None
        }
        app_wells_by_abai_id = {well.abai_id: well for well in app_wells}

        abai_ids = {well.id for well in abai_wells}
        app_abai_ids = set(app_wells_by_abai_id)

        new_abai_ids = set(abai_wells_with_name_by_id) - app_abai_ids
        deleted_abai_ids = [
            well.abai_id
            for well in app_wells
            if well.abai_id not in abai_ids and not well.is_deleted
        ]
        restored_abai_ids = [
            well.abai_id
            for well in app_wells
            if well.abai_id in abai_ids and well.is_deleted
        ]
        names_by_abai_id = {
            abai_id: abai_wells_with_name_by_id[abai_id].uwi
            for abai_id, app_well in app_wells_by_abai_id.items()
            if abai_id in abai_wells_with_name_by_id
            and abai_wells_with_name_by_id[abai_id].uwi != app_well.name
        }

        await self.well_repo.batch_create(
            [
                CreateWellDTO(
                    abai_id=abai_id,
                    name=abai_wells_with_name_by_id[abai_id].uwi,
                    # coords_id=abai_wells_with_name_by_id[abai_id].whc,
                )
                for abai_id in new_abai_ids
            ],
        )
        await self.well_repo.mark_deleted_by_abai_ids(
            deleted_abai_ids,
            is_deleted=True,
        )
        await self.well_repo.mark_deleted_by_abai_ids(
            restored_abai_ids,
            is_deleted=False,
        )
        await self.well_repo.update_names_by_abai_id(names_by_abai_id)

        await self.app_session.commit()

        logger.info(
            "Wells sync finished: created=%s, deleted=%s, restored=%s, "
            "renamed=%s, skipped_without_uwi=%s",
            len(new_abai_ids),
            len(deleted_abai_ids),
            len(restored_abai_ids),
            len(names_by_abai_id),
            len(abai_wells) - len(abai_wells_with_name_by_id),
        )


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadWells(
            abai_session=abai_session,
            app_session=app_session,
        ).run()


if __name__ == "__main__":
    asyncio.run(main())
