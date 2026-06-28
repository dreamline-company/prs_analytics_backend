from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import select, tuple_

from apps.repairs.dto.internal.repositories.reports import (
    CreateRepairSummaryDTO,
    UpdateRepairSummaryDTO,
)
from apps.repairs.models.reports import RepairSummary
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairSummaryRepository(
    AsyncAlchemyRepository[
        CreateRepairSummaryDTO,
        UpdateRepairSummaryDTO,
        RepairSummary,
    ],
):
    model = RepairSummary

    async def list_by_well_id(self, well_id: int) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.well_id == well_id,),
                order_by=(RepairSummary.date,),
            ),
        )

    async def list_by_repair_id(self, repair_id: int) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.repair_id == repair_id,),
                order_by=(RepairSummary.date,),
            ),
        )

    async def list_existing_well_date_pairs(
        self,
        pairs: Iterable[tuple[int, date]],
    ) -> set[tuple[int, date]]:
        pairs_list = list(pairs)
        if not pairs_list:
            return set()

        qs = select(RepairSummary.well_id, RepairSummary.date).where(
            tuple_(RepairSummary.well_id, RepairSummary.date).in_(pairs_list),
        )
        rows = await self.fetch_all(qs)
        return {(row["well_id"], row["date"]) for row in rows}

    async def list_by_date(self, summary_date: date) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.date == summary_date,),
                order_by=(RepairSummary.id,),
            ),
        )

    async def update_by_id(
        self,
        repair_summary_id: int,
        data: UpdateRepairSummaryDTO,
    ) -> RepairSummary:
        return await self.update(
            data=data,
            filters=(RepairSummary.id == repair_summary_id,),
        )

    async def delete_by_id(self, repair_summary_id: int) -> None:
        await self.delete(filters=(RepairSummary.id == repair_summary_id,))
