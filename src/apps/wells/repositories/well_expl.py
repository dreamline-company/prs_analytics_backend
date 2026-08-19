from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select

from apps.wells.dto.internal.repositories.well_expl import (
    CreateWellExplDTO,
    CreateWellExplTypeDTO,
    UpdateWellExplDTO,
    UpdateWellExplTypeDTO,
)
from apps.wells.models.well_expl import WellExpl, WellExplType
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellExplTypeRepository(
    AsyncAlchemyRepository[
        CreateWellExplTypeDTO,
        UpdateWellExplTypeDTO,
        WellExplType,
    ],
):
    model = WellExplType

    async def list_all(self) -> Sequence[WellExplType]:
        return await self.get_list(QuerySpec(order_by=(WellExplType.abai_id.asc(),)))

    async def get_by_abai_id(self, abai_id: int) -> WellExplType | None:
        return await self.get_one(QuerySpec(filters=(WellExplType.abai_id == abai_id,)))

    async def list_abai_ids(self) -> set[int]:
        result = await self.session.execute(select(WellExplType.abai_id))
        return set(result.scalars().all())

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellExplTypeDTO,
    ) -> WellExplType:
        return await self.update(
            data=data,
            filters=(WellExplType.abai_id == abai_id,),
        )


class WellExplRepository(
    AsyncAlchemyRepository[CreateWellExplDTO, UpdateWellExplDTO, WellExpl],
):
    model = WellExpl

    async def get_max_abai_id(self) -> int:
        result = await self.session.execute(select(func.max(WellExpl.abai_id)))
        return result.scalar() or 0

    async def get_by_abai_id(self, abai_id: int) -> WellExpl | None:
        return await self.get_one(QuerySpec(filters=(WellExpl.abai_id == abai_id,)))

    async def list_by_abai_well_id(self, abai_well_id: int) -> Sequence[WellExpl]:
        return await self.get_list(
            QuerySpec(
                filters=(WellExpl.abai_well_id == abai_well_id,),
                order_by=(WellExpl.dbeg.asc(), WellExpl.abai_id.asc()),
            ),
        )

    async def list_open_intervals(self, on_date: date) -> Sequence[WellExpl]:
        """Локальные незакрытые интервалы: dend пуст или в будущем."""
        return await self.get_list(
            QuerySpec(
                filters=((WellExpl.dend.is_(None)) | (WellExpl.dend > on_date),),
                order_by=(WellExpl.abai_id.asc(),),
            ),
        )

    async def get_latest_expl_name_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
    ) -> dict[int, str | None]:
        """Название способа эксплуатации по последнему периоду каждой скважины.

        Последний = максимальный ``dbeg`` (пустой считается самым старым),
        tiebreak по ``abai_id``. Значение ``None``, если у периода не проставлен
        способ. DISTINCT ON идёт по индексу (abai_well_id, dbeg).
        """
        if not abai_well_ids:
            return {}

        stmt = (
            select(WellExpl.abai_well_id, WellExplType.name_ru)
            .join(WellExplType, WellExplType.abai_id == WellExpl.expl, isouter=True)
            .where(WellExpl.abai_well_id.in_(abai_well_ids))
            .distinct(WellExpl.abai_well_id)
            .order_by(
                WellExpl.abai_well_id,
                WellExpl.dbeg.desc().nullslast(),
                WellExpl.abai_id.desc(),
            )
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellExplDTO,
    ) -> WellExpl:
        return await self.update(
            data=data,
            filters=(WellExpl.abai_id == abai_id,),
        )
