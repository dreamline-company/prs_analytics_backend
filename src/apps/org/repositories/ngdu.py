from collections.abc import Sequence

from sqlalchemy import insert

from apps.org.dto.internal.repositories.ngdu import CreateNGDUDTO, UpdateNGDUDTO
from apps.org.models.ngdu import NGDU
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class NGDURepository(
    AsyncAlchemyRepository[CreateNGDUDTO, UpdateNGDUDTO, NGDU],
):
    model = NGDU

    async def get_by_id(self, ngdu_id: int) -> NGDU | None:
        return await self.get_one(
            QuerySpec(
                filters=(NGDU.id == ngdu_id,),
            ),
        )

    async def get_by_name(self, name: str) -> NGDU | None:
        return await self.get_one(
            QuerySpec(
                filters=(NGDU.name == name,),
            ),
        )

    async def list_by_names(self, names: Sequence[str]) -> Sequence[NGDU]:
        if not names:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(NGDU.name.in_(names),),
                order_by=(NGDU.name,),
            ),
        )

    async def batch_create(self, data: Sequence[CreateNGDUDTO]) -> None:
        if not data:
            return

        values = [item.model_dump() for item in data]
        await self.session.execute(insert(NGDU), values)

    async def update_by_id(self, ngdu_id: int, data: UpdateNGDUDTO) -> NGDU:
        return await self.update(
            data=data,
            filters=(NGDU.id == ngdu_id,),
        )

    async def delete_by_id(self, ngdu_id: int) -> None:
        await self.delete(filters=(NGDU.id == ngdu_id,))
