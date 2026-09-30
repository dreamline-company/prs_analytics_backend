"""KPI over ``org_unique_brigade`` for one NGDU.

Fields:
  1. ``total_brigades`` — rows in ``org_unique_brigade`` for the NGDU.
  2. ``in_repair_now`` — brigades linked to a ``Repair`` with ``end_time IS
     NULL`` via ``repairs_repair_brigade``.
  3. ``with_violations`` — brigades that had at least one
     ``BrigadeErrorScreen`` (in CM) during the interval of any of their
     repairs. Mapping local UniqueBrigade → CM Brigade is by
     the number after ``№`` in the name.
  4. ``without_violations`` — ``total_brigades - with_violations``.
  5. ``avg_repair_hours`` — mean of ``end_time - start_time`` across all
     finished repairs of these brigades, in hours (``None`` if no
     finished repairs).
  6. ``frequent_repair_brigades`` — names of brigades that had more than
     five repairs in any single calendar year (ЧРФ).
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import BrigadesKPIDTO, FrequentRepairBrigadeDTO
from core import get_logger

if TYPE_CHECKING:
    from apps.org.dto.queries.brigade import GetBrigadesKPIQuery
    from apps.org.models.brigade import UniqueBrigade
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

logger = get_logger(__name__)

FREQUENT_REPAIRS_THRESHOLD = 5

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class GetBrigadesKPIUseCase:
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

    async def execute(self, query: GetBrigadesKPIQuery) -> BrigadesKPIDTO:
        brigades = await self.unique_brigade_repository.list_by_ngdu_id(query.ngdu_id)
        if not brigades:
            return BrigadesKPIDTO(
                total_brigades=0,
                in_repair_now=0,
                with_violations=0,
                without_violations=0,
                avg_repair_hours=None,
                frequent_repair_brigades=[],
            )

        brigade_by_id = {b.id: b for b in brigades}
        links = await self.repair_brigade_repository.list_by_brigade_ids(
            list(brigade_by_id),
        )
        repair_ids = list({link.repair_id for link in links})
        repairs = await self.repair_repository.list_by_ids(repair_ids)
        repairs_by_id = {r.id: r for r in repairs}

        brigade_repairs: dict[int, list[Repair]] = defaultdict(list)
        for link in links:
            repair = repairs_by_id.get(link.repair_id)
            if repair is not None:
                brigade_repairs[link.brigade_id].append(repair)

        in_repair_now = sum(
            1
            for repairs_of in brigade_repairs.values()
            if any(r.is_open for r in repairs_of)
        )

        with_violations = await self._count_with_violations(
            brigades,
            brigade_repairs,
        )

        avg_repair_hours = self._avg_finished_hours(repairs)
        frequent = self._frequent_repair_brigades(
            brigade_repairs=brigade_repairs,
            brigade_by_id=brigade_by_id,
        )

        return BrigadesKPIDTO(
            total_brigades=len(brigades),
            in_repair_now=in_repair_now,
            with_violations=with_violations,
            without_violations=len(brigades) - with_violations,
            avg_repair_hours=avg_repair_hours,
            frequent_repair_brigades=frequent,
        )

    async def _count_with_violations(
        self,
        brigades: list[UniqueBrigade],
        brigade_repairs: dict[int, list[Repair]],
    ) -> int:
        number_by_brigade_id: dict[int, str] = {}
        for brigade in brigades:
            number = self._extract_brigade_number(brigade.name)
            if number is not None:
                number_by_brigade_id[brigade.id] = number

        needed_numbers = {
            number_by_brigade_id[bid]
            for bid in brigade_repairs
            if bid in number_by_brigade_id
        }
        if not needed_numbers:
            return 0

        cm_brigades = await self.cm_brigade_repository.list_by_names(
            list(needed_numbers),
        )
        cm_ids_by_number: dict[str, list[int]] = defaultdict(list)
        for cm in cm_brigades:
            cm_ids_by_number[cm.name].append(cm.id)

        with_violations = 0
        for brigade_id, repairs_of in brigade_repairs.items():
            number = number_by_brigade_id.get(brigade_id)
            if number is None:
                continue
            cm_ids = cm_ids_by_number.get(number, [])
            if not cm_ids:
                continue
            if await self._has_violation(cm_ids, repairs_of):
                with_violations += 1

        return with_violations

    async def _has_violation(
        self,
        cm_brigade_ids: list[int],
        repairs: list[Repair],
    ) -> bool:
        finished_or_open = [r for r in repairs if r.start_time is not None]
        if not finished_or_open:
            return False

        overall_start = min(r.start_time for r in finished_or_open)
        overall_end = max(
            (r.end_time or datetime.now()) for r in finished_or_open  # noqa: DTZ005
        )
        screens_repo = self.cm_brigade_error_screen_repository
        screens = await screens_repo.list_by_brigade_ids_in_range(
            cm_brigade_ids,
            start_time=overall_start,
            end_time=overall_end,
        )
        if not screens:
            return False

        for repair in finished_or_open:
            end = repair.end_time or datetime.now()  # noqa: DTZ005
            for screen in screens:
                if repair.start_time <= screen.timestamp <= end:
                    return True
        return False

    @staticmethod
    def _avg_finished_hours(repairs: list[Repair]) -> float | None:
        deltas = [
            (r.end_time - r.start_time).total_seconds()
            for r in repairs
            if r.end_time is not None and r.start_time is not None
        ]
        if not deltas:
            return None
        return round(sum(deltas) / len(deltas) / 3600, 2)

    @staticmethod
    def _frequent_repair_brigades(
        *,
        brigade_repairs: dict[int, list[Repair]],
        brigade_by_id: dict[int, UniqueBrigade],
    ) -> list[FrequentRepairBrigadeDTO]:
        result: list[FrequentRepairBrigadeDTO] = []
        for brigade_id, repairs in brigade_repairs.items():
            counts_by_year: dict[int, int] = defaultdict(int)
            for repair in repairs:
                if repair.start_time is None:
                    continue
                counts_by_year[repair.start_time.year] += 1
            if any(c > FREQUENT_REPAIRS_THRESHOLD for c in counts_by_year.values()):
                brigade = brigade_by_id.get(brigade_id)
                if brigade is not None:
                    result.append(FrequentRepairBrigadeDTO.model_validate(brigade))
        result.sort(key=lambda b: b.name)
        return result

    @staticmethod
    def _extract_brigade_number(name: str) -> str | None:
        match = _BRIGADE_NUMBER_RE.search(name)
        return match.group(1) if match else None
