from collections.abc import Sequence
from datetime import date

from sqlalchemy import insert

from apps.telemetry.dto.internal.repositories.tech_regime import (
    CreateTechRegimeDTO,
    UpdateTechRegimeDTO,
)
from apps.telemetry.models.tech_regime import TechRegime
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class TechRegimeRepository(
    AsyncAlchemyRepository[CreateTechRegimeDTO, UpdateTechRegimeDTO, TechRegime],
):
    model = TechRegime

    async def get_by_abai_id(self, abai_id: int) -> TechRegime | None:
        return await self.get_one(
            QuerySpec(
                filters=(TechRegime.abai_id == abai_id,),
            ),
        )

    async def get_latest_by_abai_id(self) -> TechRegime | None:
        tr = await self.get_list(
            QuerySpec(
                order_by=(TechRegime.abai_id.desc(),),
                limit=1,
            ),
        )
        return tr[0] if tr else None

    async def list_by_abai_ids(
        self,
        abai_ids: Sequence[int],
    ) -> Sequence[TechRegime]:
        if not abai_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(TechRegime.abai_id.in_(abai_ids),),
                order_by=(TechRegime.abai_id.desc(),),
            ),
        )

    async def list_by_abai_well_id(
        self,
        abai_well_id: int,
    ) -> Sequence[TechRegime]:
        return await self.get_list(
            QuerySpec(
                filters=(TechRegime.abai_well_id == abai_well_id,),
                order_by=(TechRegime.start_date.desc(),),
            ),
        )

    async def list_by_abai_well_id_in_period(
        self,
        abai_well_id: int,
        *,
        start_date_from: date | None = None,
        start_date_to: date | None = None,
    ) -> Sequence[TechRegime]:
        filters = [TechRegime.abai_well_id == abai_well_id]
        if start_date_from is not None:
            filters.append(TechRegime.start_date >= start_date_from)
        if start_date_to is not None:
            filters.append(TechRegime.start_date <= start_date_to)

        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(TechRegime.start_date.asc(),),
            ),
        )

    async def list_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
    ) -> Sequence[TechRegime]:
        if not abai_well_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(TechRegime.abai_well_id.in_(abai_well_ids),),
                order_by=(TechRegime.abai_well_id.desc(), TechRegime.start_date.desc()),
            ),
        )

    async def bulk_create(self, data: Sequence[CreateTechRegimeDTO]) -> None:
        if not data:
            return

        values = [item.model_dump() for item in data]
        await self.session.execute(insert(TechRegime), values)

    async def update_by_id(
        self,
        tech_regime_id: int,
        data: UpdateTechRegimeDTO,
    ) -> TechRegime:
        return await self.update(
            data=data,
            filters=(TechRegime.id == tech_regime_id,),
        )

    async def delete_by_id(self, tech_regime_id: int) -> None:
        await self.delete(filters=(TechRegime.id == tech_regime_id,))
