from collections.abc import Sequence

from apps.repairs.dto.internal.repositories.brigade import (
    CreateRepairBrigadeDTO,
    UpdateRepairBrigadeDTO,
)
from apps.repairs.models.brigade import RepairBrigade
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairBrigadeRepository(
    AsyncAlchemyRepository[
        CreateRepairBrigadeDTO,
        UpdateRepairBrigadeDTO,
        RepairBrigade,
    ],
):
    model = RepairBrigade

    async def get_by_id(self, repair_brigade_id: int) -> RepairBrigade | None:
        return await self.get_one(
            QuerySpec(filters=(RepairBrigade.id == repair_brigade_id,)),
        )

    async def list_by_repair_id(self, repair_id: int) -> Sequence[RepairBrigade]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairBrigade.repair_id == repair_id,),
                order_by=(RepairBrigade.id,),
            ),
        )

    async def list_by_brigade_id(self, brigade_id: int) -> Sequence[RepairBrigade]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairBrigade.brigade_id == brigade_id,),
                order_by=(RepairBrigade.id,),
            ),
        )

    async def update_by_id(
        self,
        repair_brigade_id: int,
        data: UpdateRepairBrigadeDTO,
    ) -> RepairBrigade:
        return await self.update(
            data=data,
            filters=(RepairBrigade.id == repair_brigade_id,),
        )

    async def delete_by_id(self, repair_brigade_id: int) -> None:
        await self.delete(filters=(RepairBrigade.id == repair_brigade_id,))

    async def delete_by_repair_id(self, repair_id: int) -> None:
        await self.delete(filters=(RepairBrigade.repair_id == repair_id,))
