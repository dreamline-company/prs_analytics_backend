import asyncio
from typing import TYPE_CHECKING

from apps.org.dto.internal.repositories.org import CreateOrgDTO, UpdateOrgDTO
from apps.org.repositories import OrgRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIOrgRepository

if TYPE_CHECKING:
    from shared.integrations.abai.models import Org as ABAIOrg

logger = get_logger(__name__)


class LoadOrgs:
    """Sync ``org`` rows from ABAI, keyed by ``abai_id``.

    ``parent_id`` and ``org_type_id`` store the raw ABAI ids (ABAI org id /
    ABAI org_type id) — consumers resolve them via ``abai_id`` lookups, mirroring
    how repairs keep ``abai_well_id``. Orgs without a name or an org type are
    skipped since the app model requires both.
    """

    async def run(self) -> None:
        async with session_makers["abai"]() as abai_session:
            abai_orgs: list[ABAIOrg] = list(
                await ABAIOrgRepository(abai_session).get_list(),
            )

        async with session_makers["app"]() as app_session:
            app_orgs = await OrgRepository(app_session).get_list()

        app_orgs_by_abai_id = {org.abai_id: org for org in app_orgs}

        new_orgs: list[ABAIOrg] = []
        update_orgs: list[ABAIOrg] = []
        skipped = 0
        for abai_org in abai_orgs:
            if abai_org.name_ru is None or abai_org.org_type is None:
                skipped += 1
                continue

            app_org = app_orgs_by_abai_id.get(abai_org.id)
            if app_org is None:
                new_orgs.append(abai_org)
            elif (
                app_org.parent_id != abai_org.parent
                or app_org.name_ru != abai_org.name_ru
                or app_org.name_ru_short != abai_org.name_short_ru
                or app_org.org_type_id != abai_org.org_type
            ):
                update_orgs.append(abai_org)

        if not new_orgs and not update_orgs:
            logger.info(
                "Orgs already up to date. Skipped without name/type: %s",
                skipped,
            )
            return

        async with session_makers["app"]() as app_session:
            repo = OrgRepository(app_session)

            if new_orgs:
                logger.info("Creating %s new orgs...", len(new_orgs))
                await repo.bulk_create(
                    [
                        CreateOrgDTO(
                            abai_id=org.id,
                            parent_id=org.parent,
                            name_ru=org.name_ru,
                            name_ru_short=org.name_short_ru,
                            org_type_id=org.org_type,
                        )
                        for org in new_orgs
                    ],
                )
                await app_session.commit()

            if update_orgs:
                logger.info("Updating %s orgs...", len(update_orgs))
                for org in update_orgs:
                    try:
                        await repo.update_by_abai_id(
                            abai_id=org.id,
                            data=UpdateOrgDTO(
                                parent_id=org.parent,
                                name_ru=org.name_ru,
                                name_ru_short=org.name_short_ru,
                                org_type_id=org.org_type,
                            ),
                        )
                    except Exception:
                        logger.exception("Error updating org. ABAI_ID: %s", org.id)
                        continue
                await app_session.commit()

        logger.info(
            "Orgs sync finished: created=%s, updated=%s, skipped=%s",
            len(new_orgs),
            len(update_orgs),
            skipped,
        )


async def main() -> None:
    await LoadOrgs().run()


if __name__ == "__main__":
    asyncio.run(main())
