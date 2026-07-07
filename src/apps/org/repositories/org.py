from collections.abc import Sequence

from apps.org.dto.internal.repositories.org import CreateOrgDTO, UpdateOrgDTO
from apps.org.models.org import Org
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class OrgRepository(
    AsyncAlchemyRepository[CreateOrgDTO, UpdateOrgDTO, Org],
):
    model = Org

    async def get_by_id(self, org_id: int) -> Org | None:
        return await self.get_one(
            QuerySpec(
                filters=(Org.id == org_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Org | None:
        return await self.get_one(
            QuerySpec(
                filters=(Org.abai_id == abai_id,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[Org]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Org.abai_id.in_(abai_ids),),
                order_by=(Org.abai_id,),
            ),
        )

    async def update_by_abai_id(self, abai_id: int, data: UpdateOrgDTO) -> Org:
        return await self.update(
            data=data,
            filters=(Org.abai_id == abai_id,),
        )

    async def delete_by_id(self, org_id: int) -> None:
        await self.delete(filters=(Org.id == org_id,))
