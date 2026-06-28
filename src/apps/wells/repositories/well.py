from collections.abc import Sequence

from sqlalchemy import insert, update

from apps.wells.dto.internal.repositories.well import CreateWellDTO, UpdateWellDTO
from apps.wells.models.well import Well
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellRepository(
    AsyncAlchemyRepository[CreateWellDTO, UpdateWellDTO, Well],
):
    model = Well

    async def get_by_id(self, id_: int) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.id == id_,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.abai_id == abai_id,),
            ),
        )

    async def get_by_name(self, name: str) -> Well | None:
        return await self.get_one(
            QuerySpec(
                filters=(Well.name == name,),
            ),
        )

    async def list_by_abai_ids(self, abai_ids: Sequence[int]) -> Sequence[Well]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Well.abai_id.in_(abai_ids),),
                order_by=(Well.abai_id,),
            ),
        )

    async def search_by_name(
        self,
        name: str,
        *,
        limit: int = 20,
    ) -> Sequence[Well]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    Well.name.ilike(f"%{name}%"),
                    Well.is_deleted.is_(False),
                ),
                order_by=(Well.name,),
                limit=limit,
            ),
        )

    async def list_by_names(self, names: Sequence[str]) -> Sequence[Well]:
        if not names:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Well.name.in_(names),),
                order_by=(Well.name,),
            ),
        )

    async def batch_create(self, data: Sequence[CreateWellDTO]) -> None:
        if not data:
            return

        values = [item.model_dump() for item in data]
        await self.session.execute(insert(Well), values)

    async def mark_deleted_by_abai_ids(
        self,
        abai_ids: Sequence[int],
        *,
        is_deleted: bool,
    ) -> None:
        if not abai_ids:
            return

        await self.session.execute(
            update(Well)
            .where(Well.abai_id.in_(abai_ids))
            .values(is_deleted=is_deleted),
        )

    async def update_names_by_abai_id(self, names_by_abai_id: dict[int, str]) -> None:
        for abai_id, name in names_by_abai_id.items():
            await self.update(
                data=UpdateWellDTO(name=name),
                filters=(Well.abai_id == abai_id,),
            )

    async def update_by_id(self, well_id: int, data: UpdateWellDTO) -> Well:
        return await self.update(
            data=data,
            filters=(Well.id == well_id,),
        )

    async def delete_by_id(self, well_id: int) -> None:
        await self.delete(filters=(Well.id == well_id,))
