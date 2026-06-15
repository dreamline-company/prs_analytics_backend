from collections.abc import Sequence

from apps.wells.dto.internal.repositories.coords import (
    CreateCoordDTO,
    CreateWellCoordDTO,
    UpdateCoordDTO,
    UpdateWellCoordDTO,
)
from apps.wells.models.coords import Coord, WellCoord
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class CoordRepository(
    AsyncAlchemyRepository[CreateCoordDTO, UpdateCoordDTO, Coord],
):
    model = Coord

    async def get_by_abai_id(self, abai_id: int) -> Coord | None:
        return await self.get_one(
            QuerySpec(
                filters=(Coord.abai_id == abai_id,),
            ),
        )

    async def get_by_mn(self, mn: str) -> Coord | None:
        return await self.get_one(
            QuerySpec(
                filters=(Coord.mn == mn,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[Coord]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Coord.abai_id.in_(abai_ids),),
                order_by=(Coord.abai_id,),
            ),
        )

    async def update_by_id(self, coord_id: int, data: UpdateCoordDTO) -> Coord:
        return await self.update(
            data=data,
            filters=(Coord.id == coord_id,),
        )

    async def delete_by_id(self, coord_id: int) -> None:
        await self.delete(filters=(Coord.id == coord_id,))


class WellCoordRepository(
    AsyncAlchemyRepository[CreateWellCoordDTO, UpdateWellCoordDTO, WellCoord],
):
    model = WellCoord

    async def get_by_abai_id(self, abai_id: int) -> WellCoord | None:
        return await self.get_one(
            QuerySpec(
                filters=(WellCoord.abai_id == abai_id,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[WellCoord]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellCoord.abai_id.in_(abai_ids),),
                order_by=(WellCoord.abai_id,),
            ),
        )

    async def list_by_coords_system_id(
        self,
        coords_system_id: int,
    ) -> Sequence[WellCoord]:
        return await self.get_list(
            QuerySpec(
                filters=(WellCoord.coords_system_id == coords_system_id,),
                order_by=(WellCoord.abai_id,),
            ),
        )

    async def update_by_id(
        self,
        well_coord_id: int,
        data: UpdateWellCoordDTO,
    ) -> WellCoord:
        return await self.update(
            data=data,
            filters=(WellCoord.id == well_coord_id,),
        )

    async def delete_by_id(self, well_coord_id: int) -> None:
        await self.delete(filters=(WellCoord.id == well_coord_id,))
