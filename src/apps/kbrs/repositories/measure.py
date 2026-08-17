from collections.abc import Sequence

from apps.kbrs.dto.internal.repositories.measure import (
    CreateKbrsMeasureDTO,
    UpdateKbrsMeasureDTO,
)
from apps.kbrs.models.measure import KbrsMeasure
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class KbrsMeasureRepository(
    AsyncAlchemyRepository[CreateKbrsMeasureDTO, UpdateKbrsMeasureDTO, KbrsMeasure],
):
    model = KbrsMeasure

    async def map_by_measure_ids(
        self,
        measure_ids: Sequence[int],
    ) -> dict[int, KbrsMeasure]:
        if not measure_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(filters=(KbrsMeasure.measure_id.in_(measure_ids),)),
        )
        return {row.measure_id: row for row in rows}

    async def update_by_id(
        self,
        row_id: int,
        data: UpdateKbrsMeasureDTO,
    ) -> KbrsMeasure:
        return await self.update(data=data, filters=(KbrsMeasure.id == row_id,))
