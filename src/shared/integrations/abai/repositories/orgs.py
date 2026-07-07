from collections.abc import Sequence

from shared.integrations.abai.models import Org
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIOrgRepository(
    ABAIReadOnlyRepository[Org],
):
    model = Org

    async def list_by_ids(self, org_ids: Sequence[int]) -> Sequence[Org]:
        if not org_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Org.id.in_(org_ids),),
                order_by=(Org.id,),
            ),
        )

    async def list_by_parent(self, parent_id: int) -> Sequence[Org]:
        return await self.get_list(
            QuerySpec(
                filters=(Org.parent == parent_id,),
                order_by=(Org.id,),
            ),
        )
