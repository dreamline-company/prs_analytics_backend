from collections.abc import Sequence
from datetime import date

from apps.repairs.dto.internal.repositories.reports import (
    CreateRepairSummaryDTO,
    UpdateRepairSummaryDTO,
)
from apps.repairs.models.reports import RepairSummary
from shared.repository.base import AsyncAlchemyRepository, QuerySpec


class RepairSummaryRepository(
    AsyncAlchemyRepository[
        CreateRepairSummaryDTO,
        UpdateRepairSummaryDTO,
        RepairSummary,
    ],
):
    model = RepairSummary

    async def get_by_id(self, repair_summary_id: int) -> RepairSummary | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairSummary.id == repair_summary_id,),
            ),
        )

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
