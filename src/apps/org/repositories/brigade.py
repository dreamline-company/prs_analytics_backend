from collections.abc import Sequence

from apps.org.dto.internal.repositories.brigade import (
    CreateBrigadeDTO,
    UpdateBrigadeDTO,
)
from apps.org.models.brigade import Brigade
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class BrigadeRepository(
    AsyncAlchemyRepository[CreateBrigadeDTO, UpdateBrigadeDTO, Brigade],
):
    model = Brigade

    async def get_by_id(self, brigade_id: int) -> Brigade | None:
        return await self.get_one(
            QuerySpec(
                filters=(Brigade.id == brigade_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Brigade | None:
        return await self.get_one(
            QuerySpec(
                filters=(Brigade.abai_id == abai_id,),
            ),
        )

    async def list_by_org_id(self, org_id: int) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.org_id == org_id,),
                order_by=(Brigade.abai_id,),
            ),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateBrigadeDTO,
    ) -> Brigade:
        return await self.update(
            data=data,
            filters=(Brigade.abai_id == abai_id,),
        )

    async def delete_by_id(self, brigade_id: int) -> None:
        await self.delete(filters=(Brigade.id == brigade_id,))
