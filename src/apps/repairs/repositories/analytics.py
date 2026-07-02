from collections.abc import Sequence

from apps.repairs.dto.internal.repositories.analytics import (
    CreateRepairAnalyticsDTO,
    CreateRepairAnalyticsDynamogramDTO,
    CreateRepairAnalyticsSPODTO,
    UpdateRepairAnalyticsDTO,
    UpdateRepairAnalyticsDynamogramDTO,
    UpdateRepairAnalyticsSPODTO,
)
from apps.repairs.models.analytics import (
    RepairAnalytics,
    RepairAnalyticsDynamogram,
    RepairAnalyticsSPO,
)
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairAnalyticsRepository(
    AsyncAlchemyRepository[
        CreateRepairAnalyticsDTO,
        UpdateRepairAnalyticsDTO,
        RepairAnalytics,
    ],
):
    model = RepairAnalytics

    async def get_by_repair_id(self, repair_id: int) -> RepairAnalytics | None:
        return await self.get_one(
            QuerySpec(filters=(RepairAnalytics.repair_id == repair_id,)),
        )

    async def list_by_repair_ids(
        self,
        repair_ids: Sequence[int],
    ) -> Sequence[RepairAnalytics]:
        if not repair_ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(RepairAnalytics.repair_id.in_(repair_ids),)),
        )

    async def update_by_repair_id(
        self,
        repair_id: int,
        data: UpdateRepairAnalyticsDTO,
    ) -> RepairAnalytics:
        return await self.update(
            data=data,
            filters=(RepairAnalytics.repair_id == repair_id,),
        )


class RepairAnalyticsDynamogramRepository(
    AsyncAlchemyRepository[
        CreateRepairAnalyticsDynamogramDTO,
        UpdateRepairAnalyticsDynamogramDTO,
        RepairAnalyticsDynamogram,
    ],
):
    model = RepairAnalyticsDynamogram

    async def get_by_analytics_id(
        self,
        analytics_id: int,
    ) -> RepairAnalyticsDynamogram | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairAnalyticsDynamogram.analytics_id == analytics_id,),
            ),
        )

    async def update_by_analytics_id(
        self,
        analytics_id: int,
        data: UpdateRepairAnalyticsDynamogramDTO,
    ) -> RepairAnalyticsDynamogram:
        return await self.update(
            data=data,
            filters=(RepairAnalyticsDynamogram.analytics_id == analytics_id,),
        )


class RepairAnalyticsSPORepository(
    AsyncAlchemyRepository[
        CreateRepairAnalyticsSPODTO,
        UpdateRepairAnalyticsSPODTO,
        RepairAnalyticsSPO,
    ],
):
    model = RepairAnalyticsSPO

    async def get_by_analytics_id(
        self,
        analytics_id: int,
    ) -> RepairAnalyticsSPO | None:
        return await self.get_one(
            QuerySpec(filters=(RepairAnalyticsSPO.analytics_id == analytics_id,)),
        )

    async def update_by_analytics_id(
        self,
        analytics_id: int,
        data: UpdateRepairAnalyticsSPODTO,
    ) -> RepairAnalyticsSPO:
        return await self.update(
            data=data,
            filters=(RepairAnalyticsSPO.analytics_id == analytics_id,),
        )
