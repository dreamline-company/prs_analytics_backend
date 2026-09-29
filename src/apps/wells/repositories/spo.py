from collections.abc import Mapping, Sequence
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, column, func, select, values

from apps.kbrs.models.measure import KbrsMeasure
from apps.wells.dto.internal.repositories.spo import CreateSPODTO, UpdateSPODTO
from apps.wells.models.spo import SPO
from apps.wells.models.spo_event import SPOEvent
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class SPORepository(
    AsyncAlchemyRepository[CreateSPODTO, UpdateSPODTO, SPO],
):
    model = SPO

    async def list_by_well_id(self, well_id: int) -> Sequence[SPO]:
        return await self.get_list(
            QuerySpec(
                filters=(SPO.well_id == well_id,),
                order_by=(SPO.snapshot_time,),
            ),
        )

    async def map_last_work_codes(
        self,
        since_by_well_id: Mapping[int, datetime],
    ) -> dict[int, int]:
        """Код последней работы по скважине — из СПО не раньше её момента.

        СПО учитывается, если её замер шёл после ``since`` (у СПО без замера
        опросчика — начался). События СПО пишутся по порядку времени, поэтому
        последнее — с наибольшим ``id``.
        """
        if not since_by_well_id:
            return {}
        since = values(
            column("well_id", BigInteger),
            column("since", DateTime),
            name="since_by_well",
        ).data(list(since_by_well_id.items()))
        stmt = (
            select(SPO.well_id, SPOEvent.code)
            .distinct(SPO.well_id)
            .join(since, since.c.well_id == SPO.well_id)
            .join(SPOEvent, SPOEvent.spo_id == SPO.id)
            .outerjoin(KbrsMeasure, KbrsMeasure.measure_id == SPO.kbrs_measure_id)
            .where(
                SPOEvent.code.is_not(None),
                func.coalesce(KbrsMeasure.end_time, SPO.snapshot_time) >= since.c.since,
            )
            .order_by(SPO.well_id, SPO.snapshot_time.desc(), SPOEvent.id.desc())
        )
        rows = await self.session.execute(stmt)
        return dict(rows.tuples().all())

    async def get_in_window(
        self,
        well_id: int,
        start: datetime,
        end: datetime | None,
    ) -> SPO | None:
        filters = [SPO.well_id == well_id, SPO.snapshot_time >= start]
        if end is not None:
            filters.append(SPO.snapshot_time <= end)
        rs = await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(SPO.snapshot_time.asc(),),
                limit=1,
            ),
        )
        return rs[0] if rs else None

    async def get_by_kbrs_measure_id(
        self,
        kbrs_measure_id: int,
        *,
        well_id: int,
    ) -> SPO | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    SPO.kbrs_measure_id == kbrs_measure_id,
                    SPO.well_id == well_id,
                ),
            ),
        )

    async def get_by_well_id_and_snapshot_time(
        self,
        well_id: int,
        snapshot_time: datetime,
    ) -> SPO | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    SPO.well_id == well_id,
                    SPO.snapshot_time == snapshot_time,
                ),
            ),
        )

    async def list_by_well_id_in_window(
        self,
        well_id: int,
        start: datetime,
        end: datetime | None,
    ) -> Sequence[SPO]:
        filters = [SPO.well_id == well_id, SPO.snapshot_time >= start]
        if end is not None:
            filters.append(SPO.snapshot_time <= end)
        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(SPO.snapshot_time.asc(),),
            ),
        )

    async def list_by_file_id(self, file_id: int) -> Sequence[SPO]:
        return await self.get_list(
            QuerySpec(
                filters=(SPO.file_id == file_id,),
                order_by=(SPO.snapshot_time,),
            ),
        )

    async def update_by_id(self, spo_id: int, data: UpdateSPODTO) -> SPO:
        return await self.update(
            data=data,
            filters=(SPO.id == spo_id,),
        )

    async def delete_by_id(self, spo_id: int) -> None:
        await self.delete(filters=(SPO.id == spo_id,))
