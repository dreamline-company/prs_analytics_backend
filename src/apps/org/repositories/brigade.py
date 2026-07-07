from collections.abc import Sequence

from apps.org.dto.internal.repositories.brigade import (
    CreateBrigadeDTO,
    CreateUniqueBrigadeDTO,
    UpdateBrigadeDTO,
    UpdateUniqueBrigadeDTO,
)
from apps.org.models.brigade import Brigade, UniqueBrigade
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


class UniqueBrigadeRepository(
    AsyncAlchemyRepository[
        CreateUniqueBrigadeDTO,
        UpdateUniqueBrigadeDTO,
        UniqueBrigade,
    ],
):
    model = UniqueBrigade

    async def get_by_id(self, unique_brigade_id: int) -> UniqueBrigade | None:
        return await self.get_one(
            QuerySpec(filters=(UniqueBrigade.id == unique_brigade_id,)),
        )

    async def get_by_name(self, name: str) -> UniqueBrigade | None:
        return await self.get_one(
            QuerySpec(filters=(UniqueBrigade.name == name,)),
        )

    async def list_all(self) -> Sequence[UniqueBrigade]:
        return await self.get_list(QuerySpec(order_by=(UniqueBrigade.name,)))

    async def list_by_ngdu_id(self, ngdu_id: int) -> Sequence[UniqueBrigade]:
        return await self.get_list(
            QuerySpec(
                filters=(UniqueBrigade.ngdu_id == ngdu_id,),
                order_by=(UniqueBrigade.name,),
            ),
        )

    async def update_by_id(
        self,
        unique_brigade_id: int,
        data: UpdateUniqueBrigadeDTO,
    ) -> UniqueBrigade:
        return await self.update(
            data=data,
            filters=(UniqueBrigade.id == unique_brigade_id,),
        )

    async def delete_by_id(self, unique_brigade_id: int) -> None:
        await self.delete(filters=(UniqueBrigade.id == unique_brigade_id,))
