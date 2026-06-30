from collections.abc import Sequence

from shared.integrations.cm.models import Brigade
from shared.integrations.cm.repositories.base import CMReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class CMBrigadeRepository(
    CMReadOnlyRepository[Brigade],
):
    model = Brigade

    async def get_by_id(self, brigade_id: int) -> Brigade | None:
        return await self.get_one(
            QuerySpec(
                filters=(Brigade.id == brigade_id,),
            ),
        )

    async def list_by_ids(self, brigade_ids: Sequence[int]) -> Sequence[Brigade]:
        if not brigade_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Brigade.id.in_(brigade_ids),),
                order_by=(Brigade.id,),
            ),
        )

    async def list_by_ngdu_id(self, ngdu_id: int) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.ngdu_id == ngdu_id,),
                order_by=(Brigade.name,),
            ),
        )

    async def list_by_name(self, name: str) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.name == name,),
                order_by=(Brigade.id,),
            ),
        )

    async def list_by_cdng(self, cdng: str) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.cdng == cdng,),
                order_by=(Brigade.name,),
            ),
        )

    async def list_by_field(self, field: str) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.field == field,),
                order_by=(Brigade.name,),
            ),
        )
