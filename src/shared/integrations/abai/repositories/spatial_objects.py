from collections.abc import Sequence

from shared.integrations.abai.models import SpatialObject
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAISpatialObjectRepository(
    ABAIReadOnlyRepository[SpatialObject],
):
    model = SpatialObject

    async def get_by_id(self, spatial_object_id: int) -> SpatialObject | None:
        return await super().get_by_id(spatial_object_id)

    async def list_by_ids(
        self,
        spatial_object_ids: Sequence[int],
    ) -> Sequence[SpatialObject]:
        if not spatial_object_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(SpatialObject.id.in_(spatial_object_ids),),
                order_by=(SpatialObject.id,),
            ),
        )

    async def list_by_coord_system(self, coord_system_id: int) -> Sequence[SpatialObject]:
        return await self.get_list(
            QuerySpec(
                filters=(SpatialObject.coord_system == coord_system_id,),
                order_by=(SpatialObject.id,),
            ),
        )
