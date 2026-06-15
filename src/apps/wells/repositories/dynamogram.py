from collections.abc import Sequence

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
