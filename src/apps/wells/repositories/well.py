from collections.abc import Sequence

from apps.wells.dto.internal.repositories.well import CreateWellDTO, UpdateWellDTO
from apps.wells.models.well import Well
from shared.repository.base import AsyncAlchemyRepository, QuerySpec


class WellRepository(
    AsyncAlchemyRepository[CreateWellDTO, UpdateWellDTO, Well],
):
    model = Well

    async def get_by_id(self, well_id: int) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.id == well_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.abai_id == abai_id,),
            ),
        )

    async def get_by_name(self, name: str) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.name == name,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[Well]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Well.abai_id.in_(abai_ids),),
                order_by=(Well.abai_id,),
            ),
        )

    async def list_by_names(self, names: Sequence[str]) -> Sequence[Well]:
        if not names:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Well.name.in_(names),),
                order_by=(Well.name,),
            ),
        )

    async def update_by_id(self, well_id: int, data: UpdateWellDTO) -> Well:
        return await self.update(
            data=data,
            filters=(Well.id == well_id,),
        )

    async def delete_by_id(self, well_id: int) -> None:
        await self.delete(filters=(Well.id == well_id,))
