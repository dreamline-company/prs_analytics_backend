import asyncio

from apps.org.dto.internal.repositories.brigade import CreateUniqueBrigadeDTO
from apps.org.models.brigade import Brigade
from apps.org.repositories import (
    BrigadeRepository,
    OrgRepository,
    UniqueBrigadeRepository,
)
from core import get_logger
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class FillUniqueBrigades:
    """Rebuild ``org_unique_brigade`` from ``org_brigade`` rows.

    Identity is the pair ``(name_ru, ngdu_id)``. A brigade contributes only
    when its ``org_id`` points directly to an NGDU-typed org (i.e. the
    resolved ``Org.org_type_id == NGDU_ORG_TYPE``); everything else is
    skipped. Names for which *no* brigade row satisfied that are logged as
    errors once per name.

    Only inserts missing pairs. Stale pairs (present in unique but no
    longer produced) are reported, not deleted — downstream tables FK into
    ``org_unique_brigade``.
    """

    async def run(self) -> None:
        async with session_makers["app"]() as session:
            brigade_repo = BrigadeRepository(session)
            unique_repo = UniqueBrigadeRepository(session)
            org_repo = OrgRepository(session)

            brigades = await brigade_repo.get_list(
                spec=QuerySpec(order_by=(Brigade.name_ru.desc(),)),
            )
            orgs = await org_repo.list_all()
            org_by_abai_id = {o.id: o for o in orgs}

            desired: set[tuple[str, int]] = set()
            seen_names: set[str] = set()
            for brigade in brigades:
                seen_names.add(brigade.name_ru)
                if brigade.org_id is None:
                    continue
                org = org_by_abai_id.get(brigade.org_id)
                if org is None or org.org_type_id != NGDU_ORG_TYPE:
                    continue
                print("Adding Brigade: ", brigade.name_ru, org.id)
                desired.add((brigade.name_ru, org.id))

            resolved_names = {name for name, _ in desired}
            unresolved_names = sorted(seen_names - resolved_names)
            # for name in unresolved_names:
            #     logger.error(
            #         "NGDU not resolved for any brigade with name=%r — skipped.",
            #         name,
            #     )
            unresolved = len(unresolved_names)

            existing = await unique_repo.list_all()
            existing_pairs = {(row.name, row.ngdu_id) for row in existing}

            to_create = sorted(desired - existing_pairs)
            stale = existing_pairs - desired

            if not to_create:
                logger.info(
                    "UniqueBrigade already up to date. Stale (kept): %s, "
                    "unresolved brigades: %s",
                    len(stale),
                    unresolved,
                )
                return

            logger.info(
                "Creating %s unique brigade rows (stale kept: %s, "
                "unresolved brigades: %s)...",
                len(to_create),
                len(stale),
                unresolved,
            )
            await unique_repo.bulk_create(
                [
                    CreateUniqueBrigadeDTO(name=name, ngdu_id=ngdu_id)
                    for name, ngdu_id in to_create
                ],
            )
            await session.commit()

        logger.info(
            "UniqueBrigade sync finished: created=%s, stale=%s, unresolved=%s",
            len(to_create),
            len(stale),
            unresolved,
        )


async def main() -> None:
    await FillUniqueBrigades().run()


if __name__ == "__main__":
    asyncio.run(main())
