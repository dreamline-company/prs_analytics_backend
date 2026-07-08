"""Current repair-state snapshot for one ``org_unique_brigade`` row.

Resolution:
  1. Find latest ``RepairBrigade`` link for the brigade → its ``Repair``.
     Active (``end_time IS NULL``) wins over finished; among equals — the
     one with the latest ``start_time``.
  2. Load ``Well`` by ``Repair.well_id``.
  3. Count violations: extract the digit after ``№`` in the brigade name,
     find matching CM ``Brigade``(s) by that name, count
     ``BrigadeErrorScreen`` rows within the repair interval.
  4. Last event: newest ``BrigadeErrorScreen`` in the repair interval
     (nullable — brigade may have zero events).

``por_percent``, ``spo_percent`` and ``vehicles_count`` are intentional
placeholders (``0``) until the underlying metrics are implemented.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import (
    BrigadeRepairStateDTO,
    CurrentRepairDTO,
    RepairShortDTO,
    RepairStateEventDTO,
)
from apps.wells.dto.internal.well import WellShortDTO

if TYPE_CHECKING:
    from apps.org.dto.queries.brigade import GetBrigadeRepairStateQuery
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.brigade import RepairBrigade
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from apps.wells.repositories import WellRepository
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class GetBrigadeRepairStateUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        unique_brigade_repository: UniqueBrigadeRepository,
        repair_brigade_repository: RepairBrigadeRepository,
        repair_repository: RepairRepository,
        well_repository: WellRepository,
        cm_brigade_repository: CMBrigadeRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
    ) -> None:
        self.unique_brigade_repository = unique_brigade_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.repair_repository = repair_repository
        self.well_repository = well_repository
        self.cm_brigade_repository = cm_brigade_repository
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository

    async def execute(
        self,
        query: GetBrigadeRepairStateQuery,
    ) -> BrigadeRepairStateDTO:
        brigade = await self.unique_brigade_repository.get_by_id(query.brigade_id)
        if brigade is None:
            return self._empty()

        links = await self.repair_brigade_repository.list_by_brigade_id(brigade.id)
        repair = await self._pick_current_repair(links)
        if repair is None:
            return self._empty()

        well = None
        if repair.abai_well_id is not None:
            well = await self.well_repository.get_by_abai_id(
                abai_id=repair.abai_well_id,
            )
        if well is None and repair.well_id is not None:
            well = await self.well_repository.get_by_id(id_=repair.well_id)
        if well is None:
            return self._empty()

        cm_brigade_ids = await self._resolve_cm_brigade_ids(brigade.name)
        violations_count, last_event = await self._violations_and_last_event(
            cm_brigade_ids,
            repair,
        )

        return BrigadeRepairStateDTO(
            is_in_repair=True,
            current_repair=CurrentRepairDTO(
                well=WellShortDTO.model_validate(well),
                repair=RepairShortDTO.model_validate(repair),
                violations_count=violations_count,
                por_percent=0,
                spo_percent=0,
                vehicles_count=0,
                last_event=last_event,
            ),
        )

    async def _pick_current_repair(
        self,
        links: list[RepairBrigade],
    ) -> Repair | None:
        if not links:
            return None
        repair_ids = [link.repair_id for link in links]
        repairs = await self.repair_repository.list_by_ids(repair_ids)
        active = [r for r in repairs if r.end_time is None]
        if not active:
            return None
        return max(active, key=lambda r: r.start_time)

    async def _resolve_cm_brigade_ids(self, name: str) -> list[int]:
        number = self._extract_brigade_number(name)
        if number is None:
            return []
        cm_brigades = await self.cm_brigade_repository.list_by_name(number)
        return [cm.id for cm in cm_brigades]

    async def _violations_and_last_event(
        self,
        cm_brigade_ids: list[int],
        repair: Repair,
    ) -> tuple[int, RepairStateEventDTO | None]:
        if not cm_brigade_ids:
            return 0, None
        end = repair.end_time or datetime.now()  # noqa: DTZ005
        screens_repo = self.cm_brigade_error_screen_repository
        screens = await screens_repo.list_by_brigade_ids_in_range(
            cm_brigade_ids,
            start_time=repair.start_time,
            end_time=end,
        )
        if not screens:
            return 0, None
        latest = max(screens, key=lambda s: s.timestamp)
        last_event = RepairStateEventDTO(
            time=latest.timestamp,
            description=latest.description or "",
        )
        return len(screens), last_event

    @staticmethod
    def _extract_brigade_number(name: str) -> str | None:
        match = _BRIGADE_NUMBER_RE.search(name)
        return match.group(1) if match else None

    @staticmethod
    def _empty() -> BrigadeRepairStateDTO:
        return BrigadeRepairStateDTO(is_in_repair=False, current_repair=None)
