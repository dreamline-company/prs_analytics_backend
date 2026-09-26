from collections.abc import Sequence

from sqlalchemy import bindparam, func, insert, select, update

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

    async def list_abai_ids(self) -> set[int]:
        result = await self.session.execute(select(Well.abai_id))
        return set(result.scalars().all())

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

    async def list_by_names_ci(self, names: Sequence[str]) -> Sequence[Well]:
        """Скважины по именам без учёта регистра (суффиксы в БД бывают строчными:
        ``SKS_004p``, ``BLG_001n``)."""
        if not names:
            return ()
        lowered = list({name.lower() for name in names})
        return await self.get_list(
            QuerySpec(
                filters=(func.lower(Well.name).in_(lowered),),
                order_by=(Well.name,),
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

    async def update_coords_by_abai_ids(
        self,
        coords_id_by_abai_id: dict[int, int | None],
    ) -> None:
        """Проставить coords_id пачкой (один executemany вместо N UPDATE)."""
        if not coords_id_by_abai_id:
            return

        # Core-выражение по таблице, а не по модели: ORM-сессия иначе принимает
        # список параметров за bulk update по первичному ключу.
        table = Well.__table__
        stmt = (
            update(table)
            .where(table.c.abai_id == bindparam("b_abai_id"))
            .values(coords_id=bindparam("b_coords_id"))
        )
        await self.session.execute(
            stmt,
            [
                {"b_abai_id": abai_id, "b_coords_id": coords_id}
                for abai_id, coords_id in coords_id_by_abai_id.items()
            ],
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

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[Well]:
        if not ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(Well.id.in_(ids),), order_by=(Well.id,)),
        )
