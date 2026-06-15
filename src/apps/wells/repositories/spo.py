from collections.abc import Sequence

from apps.wells.dto.internal.repositories.spo import CreateSPODTO, UpdateSPODTO
from apps.wells.models.spo import SPO
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class SPORepository(
    AsyncAlchemyRepository[CreateSPODTO, UpdateSPODTO, SPO],
):
    model = SPO

    async def list_by_well_id(self, well_id: int) -> Sequence[SPO]:
        return await self.get_list(
            QuerySpec(
                filters=(SPO.well_id == well_id,),
                order_by=(SPO.snapshot_time,),
            ),
        )

    async def list_by_file_id(self, file_id: int) -> Sequence[SPO]:
        return await self.get_list(
            QuerySpec(
                filters=(SPO.file_id == file_id,),
                order_by=(SPO.snapshot_time,),
            ),
        )

    async def update_by_id(self, spo_id: int, data: UpdateSPODTO) -> SPO:
        return await self.update(
            data=data,
            filters=(SPO.id == spo_id,),
        )

    async def delete_by_id(self, spo_id: int) -> None:
        await self.delete(filters=(SPO.id == spo_id,))
