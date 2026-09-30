"""Идущий ремонт скважины — один ответ для карточки и для матрицы."""

from collections.abc import Sequence
from datetime import datetime

from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.repairs.repositories.repair import RepairRepository
from core.settings import get_settings


class CurrentRepairService:
    def __init__(self, repair_repository: RepairRepository) -> None:
        self.repair_repository = repair_repository

    async def get_for_well(self, abai_well_id: int) -> CurrentRepairDTO | None:
        current = await self.get_for_wells([abai_well_id])
        return current.get(abai_well_id)

    async def get_for_wells(
        self,
        abai_well_ids: Sequence[int],
    ) -> dict[int, CurrentRepairDTO]:
        """Ключ — ``abai_well_id``. Скважины без открытого ремонта в словарь
        не попадают."""
        return await self.repair_repository.list_current_by_abai_well_ids(
            abai_well_ids,
            # Время ремонтов ABAI — местное: по UTC ремонт, начатый меньше
            # 5 часов назад, ещё не считался идущим.
            now=datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None),
        )
