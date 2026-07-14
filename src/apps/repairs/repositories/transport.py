from collections.abc import Sequence

from apps.repairs.dto.internal.repositories.transport import (
    CreateRepairTransportDTO,
    UpdateRepairTransportDTO,
)
from apps.repairs.models.transport import RepairTransport
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairTransportRepository(
    AsyncAlchemyRepository[
        CreateRepairTransportDTO,
        UpdateRepairTransportDTO,
        RepairTransport,
    ],
):
    model = RepairTransport

    async def get_by_id(self, repair_transport_id: int) -> RepairTransport | None:
        return await self.get_one(
            QuerySpec(filters=(RepairTransport.id == repair_transport_id,)),
        )

    async def get_by_request_id(self, request_id: int) -> RepairTransport | None:
        return await self.get_one(
            QuerySpec(filters=(RepairTransport.request_id == request_id,)),
        )

    async def list_by_repair_id(
        self,
        repair_id: int,
    ) -> Sequence[RepairTransport]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairTransport.repair_id == repair_id,),
                order_by=(RepairTransport.actual_date.asc().nulls_last(),),
            ),
        )

    async def list_by_repair_ids(
        self,
        repair_ids: Sequence[int],
    ) -> Sequence[RepairTransport]:
        if not repair_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(RepairTransport.repair_id.in_(repair_ids),),
                order_by=(RepairTransport.repair_id, RepairTransport.id),
            ),
        )

    async def update_by_id(
        self,
        repair_transport_id: int,
        data: UpdateRepairTransportDTO,
    ) -> RepairTransport:
        return await self.update(
            data=data,
            filters=(RepairTransport.id == repair_transport_id,),
        )

    async def update_by_request_id(
        self,
        request_id: int,
        data: UpdateRepairTransportDTO,
    ) -> RepairTransport:
        return await self.update(
            data=data,
            filters=(RepairTransport.request_id == request_id,),
        )

    async def delete_by_id(self, repair_transport_id: int) -> None:
        await self.delete(filters=(RepairTransport.id == repair_transport_id,))

    async def delete_by_repair_id(self, repair_id: int) -> None:
        await self.delete(filters=(RepairTransport.repair_id == repair_id,))
