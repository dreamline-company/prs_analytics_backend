from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy import BigInteger, cast, func, insert, select, true
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import aliased

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

    async def get_last_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
    ) -> dict[int, TechRegime]:
        """Последний режим каждой скважины — один запрос на матрицу НГДУ.

        Как и в одиночном методе, «последний» = максимальный ``start_date``
        (действующий он или уже закончился, здесь не проверяется). LATERAL с
        ``LIMIT 1`` на скважину по индексу (abai_well_id, start_date) — вместо
        DISTINCT ON, который вычитывал и сортировал всю историю режимов
        запрошенных скважин.
        """
        if not abai_well_ids:
            return {}

        requested = select(
            func.unnest(cast(list(abai_well_ids), ARRAY(BigInteger))).label(
                "abai_well_id",
            ),
        ).subquery("requested")
        last_row = (
            select(TechRegime)
            .where(TechRegime.abai_well_id == requested.c.abai_well_id)
            .order_by(TechRegime.start_date.desc(), TechRegime.id.desc())
            .limit(1)
            .lateral("last_row")
        )
        last = aliased(TechRegime, last_row)
        stmt = select(last).select_from(requested).join(last, true())
        result = await self.session.execute(stmt)
        return {row.abai_well_id: row for row in result.scalars()}

    async def get_current_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
        *,
        on_date: date,
        grace_days: int,
    ) -> dict[int, TechRegime]:
        """Режим, действующий на дату, — для план/факт сводок.

        Режимы месячные и приезжают из ABAI с лагом, поэтому берётся последний
        по ``start_date`` режим, который уже начался и закончился не раньше
        чем ``grace_days`` назад: пока нового ещё нет, считаем по прошлому.
        Скважины без такого режима в ответ не попадают.
        """
        if not abai_well_ids:
            return {}

        requested = select(
            func.unnest(cast(list(abai_well_ids), ARRAY(BigInteger))).label(
                "abai_well_id",
            ),
        ).subquery("requested")
        current = (
            select(TechRegime)
            .where(
                TechRegime.abai_well_id == requested.c.abai_well_id,
                TechRegime.start_date <= on_date,
                TechRegime.end_date >= on_date - timedelta(days=grace_days),
            )
            .order_by(TechRegime.start_date.desc(), TechRegime.id.desc())
            .limit(1)
            .lateral("current")
        )
        regime = aliased(TechRegime, current)
        stmt = select(regime).select_from(requested).join(regime, true())
        result = await self.session.execute(stmt)
        return {row.abai_well_id: row for row in result.scalars()}

    async def get_last_by_abai_well_id(self, abai_well_id: int) -> TechRegime | None:
        regimes = await self.get_list(
            QuerySpec(
                filters=(TechRegime.abai_well_id == abai_well_id,),
                order_by=(TechRegime.start_date.desc(), TechRegime.id.desc()),
                limit=1,
            ),
        )
        return regimes[0] if regimes else None

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
