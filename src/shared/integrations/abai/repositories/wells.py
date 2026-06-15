from collections.abc import Sequence

from shared.integrations.abai.models import Well
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellRepository(
    ABAIReadOnlyRepository[Well],
):
    model = Well

    async def get_by_uwi(self, uwi: str) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.uwi == uwi,),
            ),
        )

    async def list_by_ids(self, well_ids: Sequence[int]) -> Sequence[Well]:
        if not well_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Well.id.in_(well_ids),),
                order_by=(Well.id,),
            ),
        )

    async def list_by_well_type(self, well_type_id: int) -> Sequence[Well]:
        return await self.get_list(
            QuerySpec(
                filters=(Well.well_type == well_type_id,),
                order_by=(Well.id,),
            ),
        )
