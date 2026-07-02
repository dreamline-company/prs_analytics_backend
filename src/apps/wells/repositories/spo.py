from collections.abc import Sequence
from datetime import datetime

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

    async def get_in_window(
        self,
        well_id: int,
        start: datetime,
        end: datetime | None,
    ) -> SPO | None:
        filters = [SPO.well_id == well_id, SPO.snapshot_time >= start]
        if end is not None:
            filters.append(SPO.snapshot_time <= end)
        rs = await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(SPO.snapshot_time.asc(),),
                limit=1,
            ),
        )
        return rs[0] if rs else None

    async def get_by_well_id_and_snapshot_time(
        self,
        well_id: int,
        snapshot_time: datetime,
    ) -> SPO | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    SPO.well_id == well_id,
                    SPO.snapshot_time == snapshot_time,
                ),
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
