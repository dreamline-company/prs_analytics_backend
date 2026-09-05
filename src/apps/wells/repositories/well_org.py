from collections.abc import Sequence
from datetime import date

from sqlalchemy import func, select

from apps.wells.dto.internal.repositories.well_org import (
    CreateWellOrgDTO,
    UpdateWellOrgDTO,
)
from apps.wells.models.well import Well
from apps.wells.models.well_org import WellOrg
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellOrgRepository(
    AsyncAlchemyRepository[CreateWellOrgDTO, UpdateWellOrgDTO, WellOrg],
):
    """Привязки скважин к орг. объектам.

    «Текущая» привязка скважины — строка с максимальным ``dbeg`` (пустой
    считается самым старым), tiebreak по ``abai_id``. Один и тот же порядок
    используется во всех методах, чтобы матрица скважин и разрешение НГДУ
    по скважине сходились.
    """

    model = WellOrg

    _CURRENT_ORDER = (
        WellOrg.dbeg.desc().nullslast(),
        WellOrg.abai_id.desc(),
    )

    async def get_max_abai_id(self) -> int:
        result = await self.session.execute(select(func.max(WellOrg.abai_id)))
        return result.scalar() or 0

    async def get_by_abai_id(self, abai_id: int) -> WellOrg | None:
        return await self.get_one(QuerySpec(filters=(WellOrg.abai_id == abai_id,)))

    async def list_by_abai_well_id(self, abai_well_id: int) -> Sequence[WellOrg]:
        return await self.get_list(
            QuerySpec(
                filters=(WellOrg.abai_well_id == abai_well_id,),
                order_by=(WellOrg.dbeg.asc(), WellOrg.abai_id.asc()),
            ),
        )

    async def get_current_by_abai_well_id(self, abai_well_id: int) -> WellOrg | None:
        """Текущая привязка скважины или ``None``, если привязок нет."""
        return await self.get_one(
            QuerySpec(
                filters=(WellOrg.abai_well_id == abai_well_id,),
                order_by=self._CURRENT_ORDER,
                limit=1,
            ),
        )

    async def list_current_abai_well_ids_by_abai_org_ids(
        self,
        abai_org_ids: Sequence[int],
    ) -> list[int]:
        """Скважины, чья текущая привязка лежит в заданных организациях.

        Текущая привязка выбирается по всем строкам скважины, а не только по
        строкам из ``abai_org_ids``: скважина, переведённая в другое
        подразделение, в результат не попадает. DISTINCT ON идёт по индексу
        (abai_well_id, dbeg).
        """
        if not abai_org_ids:
            return []

        current = (
            select(WellOrg.abai_well_id, WellOrg.abai_org_id)
            .distinct(WellOrg.abai_well_id)
            .order_by(WellOrg.abai_well_id, *self._CURRENT_ORDER)
            .subquery()
        )
        stmt = (
            select(current.c.abai_well_id)
            .where(current.c.abai_org_id.in_(abai_org_ids))
            .order_by(current.c.abai_well_id)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def list_current_org_by_well_name(self) -> dict[str, int]:
        """Имя скважины → ABAI id организации по текущей привязке.

        Только неудалённые скважины, у которых есть хотя бы одна привязка.
        """
        current = (
            select(WellOrg.abai_well_id, WellOrg.abai_org_id)
            .distinct(WellOrg.abai_well_id)
            .order_by(WellOrg.abai_well_id, *self._CURRENT_ORDER)
            .subquery()
        )
        stmt = (
            select(Well.name, current.c.abai_org_id)
            .join(current, current.c.abai_well_id == Well.abai_id)
            .where(Well.is_deleted.is_(False))
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def list_open_intervals(self, on_date: date) -> Sequence[WellOrg]:
        """Локальные незакрытые интервалы: dend пуст или в будущем."""
        return await self.get_list(
            QuerySpec(
                filters=((WellOrg.dend.is_(None)) | (WellOrg.dend > on_date),),
                order_by=(WellOrg.abai_id.asc(),),
            ),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateWellOrgDTO,
    ) -> WellOrg:
        return await self.update(
            data=data,
            filters=(WellOrg.abai_id == abai_id,),
        )
