from collections.abc import Sequence

from apps.repairs.dto.internal.repositories.repair import (
    CreateRepairDTO,
    CreateRepairTypeDTO,
    UpdateRepairDTO,
    UpdateRepairTypeDTO,
)
from apps.repairs.models.repair import Repair, RepairType
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairTypeRepository(
    AsyncAlchemyRepository[CreateRepairTypeDTO, UpdateRepairTypeDTO, RepairType],
):
    model = RepairType

    async def get_by_id(self, repair_type_id: int) -> RepairType | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairType.id == repair_type_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> RepairType | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairType.abai_id == abai_id,),
            ),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateRepairTypeDTO,
    ) -> RepairType:
        return await self.update(
            data=data,
            filters=(RepairType.abai_id == abai_id,),
        )

    async def delete_by_id(self, repair_type_id: int) -> None:
        await self.delete(filters=(RepairType.id == repair_type_id,))


class RepairRepository(
    AsyncAlchemyRepository[CreateRepairDTO, UpdateRepairDTO, Repair],
):
    model = Repair

    async def get_by_id(self, repair_id: int) -> Repair | None:
        return await self.get_one(
            QuerySpec(
                filters=(Repair.id == repair_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Repair | None:
        return await self.get_one(
            QuerySpec(
                filters=(Repair.abai_id == abai_id,),
            ),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[Repair]:
        return await self.get_list(
            QuerySpec(
                filters=(Repair.well_id == well_id,),
                order_by=(Repair.start_time,),
            ),
        )

    async def list_by_repair_type_id(self, repair_type_id: int) -> Sequence[Repair]:
        return await self.get_list(
            QuerySpec(
                filters=(Repair.repair_type_id == repair_type_id,),
                order_by=(Repair.start_time,),
            ),
        )

    async def update_by_id(self, repair_id: int, data: UpdateRepairDTO) -> Repair:
        return await self.update(
            data=data,
            filters=(Repair.id == repair_id,),
        )

    async def update_by_abai_id(self, abai_id: int, data: UpdateRepairDTO) -> Repair:
        return await self.update(
            data=data,
            filters=(Repair.abai_id == abai_id,),
        )

    async def delete_by_id(self, repair_id: int) -> None:
        await self.delete(filters=(Repair.id == repair_id,))

    async def get_last_by_abai_id(self) -> Repair | None:
        rs = await self.get_list(
            spec=QuerySpec(order_by=(Repair.abai_id.desc(),), limit=1),
        )
        return rs[0] if rs else None
