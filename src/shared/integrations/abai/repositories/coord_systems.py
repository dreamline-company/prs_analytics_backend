from collections.abc import Sequence

from shared.integrations.abai.models import CoordSystem
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAICoordSystemRepository(
    ABAIReadOnlyRepository[CoordSystem],
):
    model = CoordSystem

    async def list_all(self) -> Sequence[CoordSystem]:
        return await self.get_list(QuerySpec(order_by=(CoordSystem.id,)))

    async def get_by_mn(self, mn: str) -> CoordSystem | None:
        return await self.get_one(
            QuerySpec(
                filters=(CoordSystem.mn == mn,),
            ),
        )

    async def list_by_ids(
        self,
        coord_system_ids: Sequence[int],
    ) -> Sequence[CoordSystem]:
        if not coord_system_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(CoordSystem.id.in_(coord_system_ids),),
                order_by=(CoordSystem.id,),
            ),
        )
