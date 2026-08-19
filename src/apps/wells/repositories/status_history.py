from apps.wells.dto.internal.repositories.status_history import (
    CreateWellStatusHistoryDTO,
    UpdateWellStatusHistoryDTO,
)
from apps.wells.models.status_history import WellStatusHistory
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class WellStatusHistoryRepository(
    AsyncAlchemyRepository[
        CreateWellStatusHistoryDTO,
        UpdateWellStatusHistoryDTO,
        WellStatusHistory,
    ],
):
    model = WellStatusHistory

    async def get_last_by_well_id(self, well_id: int) -> WellStatusHistory | None:
        statuses = await self.get_list(
            QuerySpec(
                filters=(WellStatusHistory.well_id == well_id,),
                order_by=(
                    WellStatusHistory.created_at.desc(),
                    WellStatusHistory.id.desc(),
                ),
                limit=1,
            ),
        )
        return statuses[0] if statuses else None
