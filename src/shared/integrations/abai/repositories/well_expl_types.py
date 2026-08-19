from collections.abc import Sequence

from shared.integrations.abai.models import WellExplType
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellExplTypeRepository(
    ABAIReadOnlyRepository[WellExplType],
):
    model = WellExplType

    async def list_all(self) -> Sequence[WellExplType]:
        return await self.get_list(QuerySpec(order_by=(WellExplType.id.asc(),)))

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[WellExplType]:
        if not ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellExplType.id.in_(ids),),
                order_by=(WellExplType.id.asc(),),
            ),
        )
