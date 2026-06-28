from collections.abc import Sequence

from apps.repairs.dto.internal.repositories.docs import (
    CreateRepairDocDTO,
    UpdateRepairDocDTO,
)
from apps.repairs.models.docs import RepairDoc
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairDocRepository(
    AsyncAlchemyRepository[CreateRepairDocDTO, UpdateRepairDocDTO, RepairDoc],
):
    model = RepairDoc

    async def get_by_id(self, repair_doc_id: int) -> RepairDoc | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairDoc.id == repair_doc_id,),
            ),
        )

    async def get_by_repair_id(self, repair_id: int) -> RepairDoc | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairDoc.repair_id == repair_id,),
            ),
        )

    async def list_by_act_file_id(self, act_file_id: int) -> Sequence[RepairDoc]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairDoc.act_file_id == act_file_id,),
                order_by=(RepairDoc.id,),
            ),
        )

    async def list_by_por_file_id(self, por_file_id: int) -> Sequence[RepairDoc]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairDoc.por_file_id == por_file_id,),
                order_by=(RepairDoc.id,),
            ),
        )

    async def update_by_id(
        self,
        repair_doc_id: int,
        data: UpdateRepairDocDTO,
    ) -> RepairDoc:
        return await self.update(
            data=data,
            filters=(RepairDoc.id == repair_doc_id,),
        )

    async def update_by_repair_id(
        self,
        repair_id: int,
        data: UpdateRepairDocDTO,
    ) -> RepairDoc:
        return await self.update(
            data=data,
            filters=(RepairDoc.repair_id == repair_id,),
        )

    async def delete_by_id(self, repair_doc_id: int) -> None:
        await self.delete(filters=(RepairDoc.id == repair_doc_id,))

    async def delete_by_repair_id(self, repair_id: int) -> None:
        await self.delete(filters=(RepairDoc.repair_id == repair_id,))
