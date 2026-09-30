import asyncio

from apps.org.dto.internal.repositories.brigade import CreateUniqueBrigadeDTO
from apps.org.models.brigade import Brigade
from apps.org.repositories import (
    BrigadeRepository,
    OrgRepository,
    UniqueBrigadeRepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from core import get_logger
from shared.constants.ngdu import NGDU_ORG_TYPE
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)


class FillUniqueBrigades:
    """Rebuild ``org_unique_brigade`` from ``org_brigade`` rows.

    Identity is the pair ``(name_ru, ngdu_id)``: в ABAI у каждого НГДУ своя
    нумерация, и «Бригада №3» есть в каждом из них. Бригада попадает сюда,
    только если её орган — сам НГДУ. ``org_brigade.org_id`` хранит ABAI-id
    органа, поэтому он ищется по ``org.abai_id`` (раньше искался по
    локальному ``org.id`` — и НГДУ достались бригады чужих органов).

    Missing pairs are inserted. Stale pairs are deleted when no repair is
    linked to them; linked ones are kept and reported — ``repairs_repair_
    brigade`` FKs into ``org_unique_brigade``.
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
            org_by_abai_id = {o.abai_id: o for o in orgs}

            desired: set[tuple[str, int]] = set()
            for brigade in brigades:
                if brigade.org_id is None:
                    continue
                org = org_by_abai_id.get(brigade.org_id)
                if org is None or org.org_type_id != NGDU_ORG_TYPE:
                    continue
                desired.add((brigade.name_ru, org.id))

            existing = await unique_repo.list_all()
            existing_pairs = {(row.name, row.ngdu_id) for row in existing}
            to_create = sorted(desired - existing_pairs)
            stale = [row for row in existing if (row.name, row.ngdu_id) not in desired]
            linked = {
                link.brigade_id
                for link in await RepairBrigadeRepository(
                    session,
                ).list_by_brigade_ids([row.id for row in stale])
            }
            to_delete = [row for row in stale if row.id not in linked]

            if to_create:
                await unique_repo.bulk_create(
                    [
                        CreateUniqueBrigadeDTO(name=name, ngdu_id=ngdu_id)
                        for name, ngdu_id in to_create
                    ],
                )
            for row in to_delete:
                await unique_repo.delete_by_id(row.id)
            if to_create or to_delete:
                await session.commit()

        logger.info(
            "UniqueBrigade sync finished: created=%s, deleted=%s, "
            "stale kept (linked to repairs)=%s",
            len(to_create),
            len(to_delete),
            len(stale) - len(to_delete),
        )


async def main() -> None:
    await FillUniqueBrigades().run()


if __name__ == "__main__":
    asyncio.run(main())
