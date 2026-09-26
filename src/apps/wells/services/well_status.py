"""Текущий статус по ремонту — общий для матрицы скважин и матрицы бригад.

«СПО» — на скважине идёт спуско-подъёмная операция: есть ``repairs_spo`` с
живым замером КБРС (последняя точка не старше грейса опросчика, старт не в
будущем); иначе «ПРС» — есть незавершённый ремонт ABAI; иначе пусто.
СПО важнее ПРС: операция идёт внутри ремонта.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apps.wells.dto.internal.well_matrix import WELL_STATUS_PRS, WELL_STATUS_SPO
from core.settings import get_settings

if TYPE_CHECKING:
    from collections.abc import Iterable

    from apps.repairs.models.repair import Repair
    from apps.wells.repositories.spo import SPORepository


class WellStatusService:
    def __init__(self, spo_repository: SPORepository) -> None:
        self._spo_repository = spo_repository

    async def live_spo_well_ids(self, well_ids: Iterable[int]) -> set[int]:
        """Скважины из ``well_ids``, где прямо сейчас идёт СПО."""
        ids = sorted(set(well_ids))
        if not ids:
            return set()
        settings = get_settings()
        now = datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None)
        return await self._spo_repository.list_well_ids_with_live_measures(
            ids,
            now=now,
            grace=timedelta(minutes=settings.KBRS_POLL_REFRESH_GRACE_MINUTES),
        )

    @staticmethod
    def status(*, is_spo_live: bool, active_repair: Repair | None) -> str | None:
        if is_spo_live:
            return WELL_STATUS_SPO
        if active_repair is not None:
            return WELL_STATUS_PRS
        return None
