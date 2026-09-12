from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import or_, select, update

from apps.kbrs.dto.internal.repositories.measure import (
    CreateKbrsMeasureDTO,
    UpdateKbrsMeasureDTO,
)
from apps.kbrs.models.measure import MEASURE_STATUS_OK, KbrsMeasure
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

    async def get_by_measure_id(self, measure_id: int) -> KbrsMeasure | None:
        return await self.get_one(
            QuerySpec(filters=(KbrsMeasure.measure_id == measure_id,)),
        )

    async def list_matching(
        self,
        *,
        owner_id: int,
        well_number: int,
        start: datetime,
        end: datetime,
    ) -> Sequence[KbrsMeasure]:
        """Разобранные замеры НГДУ по номеру скважины, пересекающие интервал."""
        return await self.get_list(
            QuerySpec(
                filters=(
                    KbrsMeasure.owner_id == owner_id,
                    KbrsMeasure.well_number == well_number,
                    KbrsMeasure.status == MEASURE_STATUS_OK,
                    KbrsMeasure.start_time.is_not(None),
                    KbrsMeasure.start_time <= end,
                    or_(
                        KbrsMeasure.end_time.is_(None),
                        KbrsMeasure.end_time >= start,
                    ),
                ),
                order_by=(KbrsMeasure.start_time,),
            ),
        )

    async def list_devices_without_description(self) -> list[tuple[int, int]]:
        """Пары (owner_id, device_id), у замеров которых нет описания прибора."""
        qs = (
            select(KbrsMeasure.owner_id, KbrsMeasure.device_id)
            .where(KbrsMeasure.device_description.is_(None))
            .distinct()
        )
        rows = await self.session.execute(qs)
        return [(int(owner_id), int(device_id)) for owner_id, device_id in rows]

    async def set_device_description(
        self,
        *,
        owner_id: int,
        device_id: int,
        description: str,
    ) -> int:
        """Проставить описание всем замерам прибора; вернуть число строк."""
        result = await self.session.execute(
            update(KbrsMeasure)
            .where(
                KbrsMeasure.owner_id == owner_id,
                KbrsMeasure.device_id == device_id,
                KbrsMeasure.device_description.is_(None),
            )
            .values(device_description=description),
        )
        return result.rowcount or 0
