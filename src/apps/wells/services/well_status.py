"""Текущий статус по ремонту — общий для матрицы скважин и матрицы бригад.

При незавершённом ремонте ABAI — «Работа [n]», где n — код последней работы
из событий замеров КБРС за этот ремонт: код держится, пока не придёт новый или
ремонт не закончится. Пока кодов нет (СПО не было или ещё не пришла) — «ПРС».
Без ремонта статуса нет.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from apps.wells.dto.internal.well_matrix import WELL_STATUS_PRS, WELL_STATUS_WORK

if TYPE_CHECKING:
    from collections.abc import Mapping

    from apps.repairs.models.repair import Repair
    from apps.wells.repositories.spo import SPORepository


class WellStatusService:
    def __init__(self, spo_repository: SPORepository) -> None:
        self._spo_repository = spo_repository

    async def work_codes(
        self,
        active_repair_by_well_id: Mapping[int, Repair],
    ) -> dict[int, int]:
        """Код последней работы по скважине за её незавершённый ремонт."""
        return await self._spo_repository.map_last_work_codes(
            {
                well_id: repair.start_time
                for well_id, repair in active_repair_by_well_id.items()
            },
        )

    @staticmethod
    def status(*, active_repair: Repair | None, work_code: int | None) -> str | None:
        if active_repair is None:
            return None
        if work_code is not None:
            return WELL_STATUS_WORK.format(code=work_code)
        return WELL_STATUS_PRS
