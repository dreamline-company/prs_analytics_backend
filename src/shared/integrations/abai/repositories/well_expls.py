from collections.abc import Sequence
from datetime import date

from shared.integrations.abai.models import WellExpl
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellExplRepository(
    ABAIReadOnlyRepository[WellExpl],
):
    model = WellExpl

    async def list_after_id(self, last_id: int, *, limit: int) -> Sequence[WellExpl]:
        """Батч периодов эксплуатации с id > last_id (keyset-пагинация)."""
        return await self.get_list(
            QuerySpec(
                filters=(WellExpl.id > last_id,),
                order_by=(WellExpl.id.asc(),),
                limit=limit,
            ),
        )

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[WellExpl]:
        if not ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellExpl.id.in_(ids),),
                order_by=(WellExpl.id.asc(),),
            ),
        )

    async def list_by_well(self, well_id: int) -> Sequence[WellExpl]:
        return await self.get_list(
            QuerySpec(
                filters=(WellExpl.well == well_id,),
                order_by=(WellExpl.dbeg.asc(), WellExpl.id.asc()),
            ),
        )

    async def list_open_intervals(self, on_date: date) -> Sequence[WellExpl]:
        """Незакрытые интервалы: dend пуст или лежит в будущем.

        Источник закрывает период задним числом (правит dend у уже
        существующей строки), поэтому такие строки нужно перечитывать.
        """
        return await self.get_list(
            QuerySpec(
                filters=((WellExpl.dend.is_(None)) | (WellExpl.dend > on_date),),
                order_by=(WellExpl.id.asc(),),
            ),
        )
