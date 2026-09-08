from collections.abc import Sequence
from datetime import date

from shared.integrations.abai.models import GdisCurrent, GdisCurrentValue, Metric
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIMetricRepository(ABAIReadOnlyRepository[Metric]):
    model = Metric

    async def list_all(self) -> Sequence[Metric]:
        return await self.get_list(QuerySpec(order_by=(Metric.id.asc(),)))


class ABAIGdisCurrentRepository(ABAIReadOnlyRepository[GdisCurrent]):
    model = GdisCurrent

    async def list_after_id(
        self,
        last_id: int,
        *,
        limit: int,
        since: date | None = None,
    ) -> Sequence[GdisCurrent]:
        """Батч исследований с id > last_id (keyset); ``since`` — только с
        ``meas_date`` не раньше даты (перечитывание недавних)."""
        filters = [GdisCurrent.id > last_id]
        if since is not None:
            filters.append(GdisCurrent.meas_date >= since)
        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(GdisCurrent.id.asc(),),
                limit=limit,
            ),
        )


class ABAIGdisCurrentValueRepository(ABAIReadOnlyRepository[GdisCurrentValue]):
    model = GdisCurrentValue

    async def list_after_id(
        self,
        last_id: int,
        *,
        limit: int,
    ) -> Sequence[GdisCurrentValue]:
        return await self.get_list(
            QuerySpec(
                filters=(GdisCurrentValue.id > last_id,),
                order_by=(GdisCurrentValue.id.asc(),),
                limit=limit,
            ),
        )

    async def list_by_gdis_ids(
        self,
        gdis_ids: Sequence[int],
    ) -> Sequence[GdisCurrentValue]:
        if not gdis_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(GdisCurrentValue.gdis_curr.in_(gdis_ids),),
                order_by=(GdisCurrentValue.id.asc(),),
            ),
        )
