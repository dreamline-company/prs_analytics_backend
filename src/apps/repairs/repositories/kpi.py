from apps.repairs.dto.internal.repositories.kpi import (
    CreateRepairKPIDTO,
    UpdateRepairKPIDTO,
)
from apps.repairs.models.analytics import RepairKPI
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairKPIRepository(
    AsyncAlchemyRepository[CreateRepairKPIDTO, UpdateRepairKPIDTO, RepairKPI],
):
    model = RepairKPI

    async def get_by_analytics_id(self, analytics_id: int) -> RepairKPI | None:
        return await self.get_one(
            QuerySpec(filters=(RepairKPI.analytics_id == analytics_id,)),
        )

    async def update_by_analytics_id(
        self,
        analytics_id: int,
        data: UpdateRepairKPIDTO,
    ) -> RepairKPI:
        return await self.update(
            data=data,
            filters=(RepairKPI.analytics_id == analytics_id,),
        )
