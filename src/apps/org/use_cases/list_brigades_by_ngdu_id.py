"""List ``org_unique_brigade`` rows for an NGDU with derived per-brigade
metrics — ``is_in_repair`` and ``violations_count`` — computed against
``repairs_repair_brigade`` and CM ``BrigadeErrorScreen``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import BrigadeDTO

if TYPE_CHECKING:
    from apps.org.dto.queries.brigade import ListBrigadesByNGDUIdQuery
    from apps.org.models.brigade import UniqueBrigade
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class ListBrigadesByNGDUIdUseCase:
    def __init__(
        self,
        *,
        unique_brigade_repository: UniqueBrigadeRepository,
        repair_brigade_repository: RepairBrigadeRepository,
        repair_repository: RepairRepository,
        cm_brigade_repository: CMBrigadeRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
    ) -> None:
        self.unique_brigade_repository = unique_brigade_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.repair_repository = repair_repository
        self.cm_brigade_repository = cm_brigade_repository
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository

    async def execute(
        self,
        query: ListBrigadesByNGDUIdQuery,
    ) -> list[BrigadeDTO]:
        brigades = await self.unique_brigade_repository.list_by_ngdu_id(query.ngdu_id)
        if not brigades:
            return []

        brigade_repairs = await self._load_brigade_repairs(brigades)
        in_repair_ids = {
            bid
            for bid, repairs in brigade_repairs.items()
            if any(r.end_time is None for r in repairs)
        }
        violations_by_brigade = await self._count_violations(
            brigades,
            brigade_repairs,
        )

        result: list[BrigadeDTO] = []
        for brigade in brigades:
            dto = BrigadeDTO.model_validate(brigade)
            dto.is_in_repair = brigade.id in in_repair_ids
            dto.violations_count = violations_by_brigade.get(brigade.id, 0)
            result.append(dto)
        return result

    async def _load_brigade_repairs(
        self,
        brigades: list[UniqueBrigade],
    ) -> dict[int, list[Repair]]:
        brigade_ids = [b.id for b in brigades]
        links = await self.repair_brigade_repository.list_by_brigade_ids(brigade_ids)
        if not links:
            return {}
        repair_ids = list({link.repair_id for link in links})
        repairs = await self.repair_repository.list_by_ids(repair_ids)
        repairs_by_id = {r.id: r for r in repairs}

        result: dict[int, list[Repair]] = defaultdict(list)
        for link in links:
            repair = repairs_by_id.get(link.repair_id)
            if repair is not None:
                result[link.brigade_id].append(repair)
        return result

    async def _count_violations(
        self,
        brigades: list[UniqueBrigade],
        brigade_repairs: dict[int, list[Repair]],
    ) -> dict[int, int]:
        number_by_brigade_id = {
            b.id: number
            for b in brigades
            if b.id in brigade_repairs
            and (number := self._extract_brigade_number(b.name)) is not None
        }
        if not number_by_brigade_id:
            return {}

        cm_ids_by_number = await self._load_cm_ids_by_number(
            set(number_by_brigade_id.values()),
        )
        if not cm_ids_by_number:
            return {}

        screens_by_cm_id = await self._load_screens_by_cm_id(
            cm_ids_by_number,
            brigade_repairs,
        )
        if not screens_by_cm_id:
            return {}

        counts: dict[int, int] = {}
        for brigade_id, repairs in brigade_repairs.items():
            number = number_by_brigade_id.get(brigade_id)
            if number is None:
                continue
            cm_ids = cm_ids_by_number.get(number, [])
            candidate_screens = [
                s for cm_id in cm_ids for s in screens_by_cm_id.get(cm_id, [])
            ]
            hits = sum(
                1
                for screen in candidate_screens
                if self._within_any_repair(screen.timestamp, repairs)
            )
            if hits:
                counts[brigade_id] = hits
        return counts

    async def _load_cm_ids_by_number(
        self,
        numbers: set[str],
    ) -> dict[str, list[int]]:
        cm_brigades = await self.cm_brigade_repository.list_by_names(list(numbers))
        result: dict[str, list[int]] = defaultdict(list)
        for cm in cm_brigades:
            result[cm.name].append(cm.id)
        return result

    async def _load_screens_by_cm_id(
        self,
        cm_ids_by_number: dict[str, list[int]],
        brigade_repairs: dict[int, list[Repair]],
    ) -> dict[int, list]:
        all_cm_ids = list({cid for ids in cm_ids_by_number.values() for cid in ids})
        overall_start, overall_end = self._overall_range(brigade_repairs)
        if overall_start is None:
            return {}
        screens_repo = self.cm_brigade_error_screen_repository
        screens = await screens_repo.list_by_brigade_ids_in_range(
            all_cm_ids,
            start_time=overall_start,
            end_time=overall_end,
        )
        result: dict[int, list] = defaultdict(list)
        for screen in screens:
            result[screen.brigade_id].append(screen)
        return result

    @staticmethod
    def _overall_range(
        brigade_repairs: dict[int, list[Repair]],
    ) -> tuple[datetime | None, datetime]:
        starts = [
            r.start_time
            for repairs in brigade_repairs.values()
            for r in repairs
            if r.start_time is not None
        ]
        if not starts:
            return None, datetime.now()  # noqa: DTZ005
        ends = [
            (r.end_time or datetime.now())  # noqa: DTZ005
            for repairs in brigade_repairs.values()
            for r in repairs
        ]
        return min(starts), max(ends)

    @staticmethod
    def _within_any_repair(timestamp: datetime, repairs: list[Repair]) -> bool:
        for repair in repairs:
            if repair.start_time is None:
                continue
            end = repair.end_time or datetime.now()  # noqa: DTZ005
            if repair.start_time <= timestamp <= end:
                return True
        return False

    @staticmethod
    def _extract_brigade_number(name: str) -> str | None:
        match = _BRIGADE_NUMBER_RE.search(name)
        return match.group(1) if match else None
