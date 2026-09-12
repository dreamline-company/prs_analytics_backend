from collections.abc import Sequence
from datetime import datetime

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

    async def list_by_well_ids_in_period(
        self,
        well_ids: Sequence[int],
        *,
        since: datetime,
        until: datetime,
    ) -> dict[int, list[WellStatusHistory]]:
        """Смены статуса скважин за окно, по времени; ключ — well_id."""
        if not well_ids:
            return {}
        rows = await self.get_list(
            QuerySpec(
                filters=(
                    WellStatusHistory.well_id.in_(well_ids),
                    WellStatusHistory.created_at >= since,
                    WellStatusHistory.created_at < until,
                ),
                order_by=(
                    WellStatusHistory.created_at.asc(),
                    WellStatusHistory.id.asc(),
                ),
            ),
        )
        history: dict[int, list[WellStatusHistory]] = {}
        for row in rows:
            history.setdefault(row.well_id, []).append(row)
        return history
