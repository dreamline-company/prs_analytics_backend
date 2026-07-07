import asyncio
from typing import TYPE_CHECKING

from apps.org.dto.internal.repositories.org_type import (
    CreateOrgTypeDTO,
)
from apps.org.repositories import OrgTypeRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.repositories import ABAIOrgRepository

if TYPE_CHECKING:
    from shared.integrations.abai.models import Org as ABAIOrg

logger = get_logger(__name__)


class LoadOrgTypes:
    """Sync the distinct ``org_type`` ids referenced by ABAI orgs.

    ABAI exposes ``org_type`` only as a foreign key on the ``org`` table (there
    is no separate source table wired here), so we materialize the distinct set
    of referenced ids as ``org_type`` rows keyed by ``abai_id``.
    """

    async def run(self) -> None:
        async with session_makers["abai"]() as abai_session:
            abai_orgs: list[ABAIOrg] = list(
                await ABAIOrgRepository(abai_session).get_list(),
            )

        abai_type_ids = {
            org.org_type for org in abai_orgs if org.org_type is not None
        }

        async with session_makers["app"]() as app_session:
            repo = OrgTypeRepository(app_session)
            existing = await repo.get_list()
            existing_abai_ids = {ot.abai_id for ot in existing}

            new_ids = abai_type_ids - existing_abai_ids
            if not new_ids:
                logger.info("Org types already up to date.")
                return

            await repo.bulk_create(
                [CreateOrgTypeDTO(abai_id=type_id) for type_id in sorted(new_ids)],
            )
            await app_session.commit()

        logger.info("Org types sync finished: created=%s", len(new_ids))


async def main() -> None:
    await LoadOrgTypes().run()


if __name__ == "__main__":
    asyncio.run(main())
