import asyncio
from typing import TYPE_CHECKING

from apps.org.dto.internal.repositories.brigade import (
    CreateBrigadeDTO,
    UpdateBrigadeDTO,
)
from apps.org.repositories import BrigadeRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIBrigadeRepository

if TYPE_CHECKING:
    from shared.integrations.abai.models import Brigade as ABAIBrigade

logger = get_logger(__name__)


class LoadBrigades:
    """Sync ``org_brigade`` rows from ABAI, keyed by ``abai_id``.

    ``org_id`` stores the raw ABAI org id (resolve via ``org.abai_id``).
    Brigades without a name are skipped since the app model requires one.
    """

    async def run(self) -> None:
        async with session_makers["abai"]() as abai_session:
            abai_brigades: list[ABAIBrigade] = list(
                await ABAIBrigadeRepository(abai_session).get_list(),
            )

        async with session_makers["app"]() as app_session:
            app_brigades = await BrigadeRepository(app_session).get_list()

        app_brigades_by_abai_id = {b.abai_id: b for b in app_brigades}

        new_brigades: list[ABAIBrigade] = []
        update_brigades: list[ABAIBrigade] = []
        skipped = 0
        for abai_brigade in abai_brigades:
            if abai_brigade.name_ru is None:
                skipped += 1
                continue

            app_brigade = app_brigades_by_abai_id.get(abai_brigade.id)
            if app_brigade is None:
                new_brigades.append(abai_brigade)
            elif (
                app_brigade.name_ru != abai_brigade.name_ru
                or app_brigade.name_ru_short != abai_brigade.name_short_ru
                or app_brigade.own != abai_brigade.own
                or app_brigade.org_id != abai_brigade.org
            ):
                update_brigades.append(abai_brigade)

        if not new_brigades and not update_brigades:
            logger.info(
                "Brigades already up to date. Skipped without name: %s",
                skipped,
            )
            return

        async with session_makers["app"]() as app_session:
            repo = BrigadeRepository(app_session)

            if new_brigades:
                logger.info("Creating %s new brigades...", len(new_brigades))
                await repo.bulk_create(
                    [
                        CreateBrigadeDTO(
                            abai_id=b.id,
                            name_ru=b.name_ru,
                            name_ru_short=b.name_short_ru,
                            own=b.own,
                            org_id=b.org,
                        )
                        for b in new_brigades
                    ],
                )
                await app_session.commit()

            if update_brigades:
                logger.info("Updating %s brigades...", len(update_brigades))
                for b in update_brigades:
                    try:
                        await repo.update_by_abai_id(
                            abai_id=b.id,
                            data=UpdateBrigadeDTO(
                                name_ru=b.name_ru,
                                name_ru_short=b.name_short_ru,
                                own=b.own,
                                org_id=b.org,
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Error updating brigade. ABAI_ID: %s",
                            b.id,
                        )
                        continue
                await app_session.commit()

        logger.info(
            "Brigades sync finished: created=%s, updated=%s, skipped=%s",
            len(new_brigades),
            len(update_brigades),
            skipped,
        )


async def main() -> None:
    await LoadBrigades().run()


if __name__ == "__main__":
    asyncio.run(main())
