from collections.abc import Sequence

from sqlalchemy import delete

from apps.wells.dto.internal.repositories.spo_event import (
    CreateSPOEventDTO,
    UpdateSPOEventDTO,
)
from apps.wells.models.spo_event import SPOEvent
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class SPOEventRepository(
    AsyncAlchemyRepository[CreateSPOEventDTO, UpdateSPOEventDTO, SPOEvent],
):
    model = SPOEvent

    async def list_by_spo_id(self, spo_id: int) -> Sequence[SPOEvent]:
        return await self.get_list(
            QuerySpec(
                filters=(SPOEvent.spo_id == spo_id,),
                order_by=(SPOEvent.offset.asc(),),
            ),
        )

    async def delete_by_spo_id(self, spo_id: int) -> None:
        await self.session.execute(
            delete(SPOEvent).where(SPOEvent.spo_id == spo_id),
        )

    async def bulk_create(self, data: Sequence[CreateSPOEventDTO]) -> None:
        if not data:
            return
        for dto in data:
            await self.create(dto)

    async def replace_for_spo(
        self,
        spo_id: int,
        data: Sequence[CreateSPOEventDTO],
    ) -> None:
        await self.delete_by_spo_id(spo_id)
        await self.bulk_create(data)
