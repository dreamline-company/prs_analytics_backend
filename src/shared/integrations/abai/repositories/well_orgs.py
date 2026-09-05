from collections.abc import Sequence

from shared.integrations.abai.models import WellOrg
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellOrgRepository(
    ABAIReadOnlyRepository[WellOrg],
):
    model = WellOrg

    async def list_after_id(self, last_id: int, *, limit: int) -> Sequence[WellOrg]:
        """Батч привязок с id > last_id (keyset-пагинация)."""
        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.id > last_id,),
                order_by=(WellOrg.id.asc(),),
                limit=limit,
            ),
        )

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[WellOrg]:
        if not ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.id.in_(ids),),
                order_by=(WellOrg.id.asc(),),
            ),
        )

    async def list_by_well(self, well_id: int) -> Sequence[WellOrg]:
        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.well == well_id,),
                order_by=(WellOrg.id,),
            ),
        )

    async def list_by_org(self, org_id: int) -> Sequence[WellOrg]:
        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.org == org_id,),
                order_by=(WellOrg.id,),
            ),
        )

    async def list_by_orgs(self, org_ids: Sequence[int]) -> Sequence[WellOrg]:
        if not org_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.org.in_(org_ids),),
                order_by=(WellOrg.id,),
            ),
        )
