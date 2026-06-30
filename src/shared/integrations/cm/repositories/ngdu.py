from collections.abc import Sequence

from shared.integrations.cm.models import NGDU
from shared.integrations.cm.repositories.base import CMReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class CMNGDURepository(
    CMReadOnlyRepository[NGDU],
):
    model = NGDU

    async def get_by_id(self, ngdu_id: int) -> NGDU | None:
        return await self.get_one(
            QuerySpec(
                filters=(NGDU.id == ngdu_id,),
            ),
        )

    async def get_by_name(self, name: str) -> NGDU | None:
        return await self.get_one(
            QuerySpec(
                filters=(NGDU.name == name,),
            ),
        )

    async def list_by_ids(self, ngdu_ids: Sequence[int]) -> Sequence[NGDU]:
        if not ngdu_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(NGDU.id.in_(ngdu_ids),),
                order_by=(NGDU.id,),
            ),
        )

    async def list_all(self) -> Sequence[NGDU]:
        return await self.get_list(
            QuerySpec(
                order_by=(NGDU.name,),
            ),
        )
