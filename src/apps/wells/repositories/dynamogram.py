from collections.abc import Sequence
from datetime import datetime

from apps.wells.dto.internal.repositories.dynamogram import (
    CreateDynamogramDTO,
    UpdateDynamogramDTO,
)
from apps.wells.models.dynamogram import Dynamogram
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class DynamogramRepository(
    AsyncAlchemyRepository[CreateDynamogramDTO, UpdateDynamogramDTO, Dynamogram],
):
    model = Dynamogram

    async def list_by_well_id(self, well_id: int) -> Sequence[Dynamogram]:
        return await self.get_list(
            QuerySpec(
                filters=(Dynamogram.well_id == well_id,),
                order_by=(Dynamogram.snapshot_time,),
            ),
        )

    async def get_closest_before(
        self,
        well_id: int,
        at: datetime,
    ) -> Dynamogram | None:
        rs = await self.get_list(
            QuerySpec(
                filters=(
                    Dynamogram.well_id == well_id,
                    Dynamogram.snapshot_time <= at,
                ),
                order_by=(Dynamogram.snapshot_time.desc(),),
                limit=1,
            ),
        )
        return rs[0] if rs else None

    async def get_closest_after(
        self,
        well_id: int,
        at: datetime,
    ) -> Dynamogram | None:
        rs = await self.get_list(
            QuerySpec(
                filters=(
                    Dynamogram.well_id == well_id,
                    Dynamogram.snapshot_time >= at,
                ),
                order_by=(Dynamogram.snapshot_time.asc(),),
                limit=1,
            ),
        )
        return rs[0] if rs else None

    async def get_by_well_id_and_snapshot_time(
        self,
        well_id: int,
        snapshot_time: datetime,
    ) -> Dynamogram | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    Dynamogram.well_id == well_id,
                    Dynamogram.snapshot_time == snapshot_time,
                ),
            ),
        )

    async def list_by_ids(
        self,
        dynamogram_ids: Sequence[int],
    ) -> Sequence[Dynamogram]:
        if not dynamogram_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(Dynamogram.id.in_(dynamogram_ids),),
                order_by=(Dynamogram.id,),
            ),
        )

    async def list_by_file_id(self, file_id: int) -> Sequence[Dynamogram]:
        return await self.get_list(
            QuerySpec(
                filters=(Dynamogram.file_id == file_id,),
                order_by=(Dynamogram.snapshot_time,),
            ),
        )

    async def update_by_id(
        self,
        dynamogram_id: int,
        data: UpdateDynamogramDTO,
    ) -> Dynamogram:
        return await self.update(
            data=data,
            filters=(Dynamogram.id == dynamogram_id,),
        )

    async def delete_by_id(self, dynamogram_id: int) -> None:
        await self.delete(filters=(Dynamogram.id == dynamogram_id,))
