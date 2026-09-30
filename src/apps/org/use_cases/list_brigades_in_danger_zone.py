"""Brigades in the danger zone: currently on an active repair with at
least one associated danger (currently only CM ``BrigadeErrorScreen``
matched to the brigade's ``№<n>`` name).

Only brigades that (a) are linked to an active ``Repair`` (``end_time IS
NULL``) via ``repairs_repair_brigade`` and (b) accumulated at least one
danger within that repair's interval are returned.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import (
    BrigadeDangerDTO,
    BrigadeDangerZoneItemDTO,
    BrigadeShortDTO,
)

if TYPE_CHECKING:
    from apps.org.dto.queries.brigade import ListBrigadesInDangerZoneQuery
    from apps.org.models.brigade import UniqueBrigade
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from shared.integrations.cm.models import BrigadeErrorScreen
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

DANGER_TYPE_VIOLATION = "violation"

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class ListBrigadesInDangerZoneUseCase:
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
        query: ListBrigadesInDangerZoneQuery,
    ) -> list[BrigadeDangerZoneItemDTO]:
        brigades = await self.unique_brigade_repository.list_by_ngdu_id(query.ngdu_id)
        if not brigades:
            return []

        active_by_brigade = await self._load_active_repairs(brigades)
        if not active_by_brigade:
            return []

        active_brigades = [b for b in brigades if b.id in active_by_brigade]
        screens_by_brigade = await self._load_violation_screens(
            active_brigades,
            active_by_brigade,
        )

        result: list[BrigadeDangerZoneItemDTO] = []
        for brigade in active_brigades:
            screens = screens_by_brigade.get(brigade.id, [])
            if not screens:
                continue
            dangers = [
                BrigadeDangerDTO(
                    type=DANGER_TYPE_VIOLATION,
                    time=s.timestamp,
                    description=s.description or "",
                )
                for s in sorted(screens, key=lambda s: s.timestamp, reverse=True)
            ]
            result.append(
                BrigadeDangerZoneItemDTO(
                    brigade=BrigadeShortDTO.model_validate(brigade),
                    dangers=dangers,
                ),
            )
        return result

    async def _load_active_repairs(
        self,
        brigades: list[UniqueBrigade],
    ) -> dict[int, Repair]:
        brigade_ids = [b.id for b in brigades]
        links = await self.repair_brigade_repository.list_by_brigade_ids(brigade_ids)
        if not links:
            return {}
        repair_ids = list({link.repair_id for link in links})
        repairs = await self.repair_repository.list_by_ids(repair_ids)
        repairs_by_id = {r.id: r for r in repairs if r.is_open}

        result: dict[int, Repair] = {}
        for link in links:
            repair = repairs_by_id.get(link.repair_id)
            if repair is None:
                continue
            existing = result.get(link.brigade_id)
            if existing is None or repair.start_time > existing.start_time:
                result[link.brigade_id] = repair
        return result

    async def _load_violation_screens(
        self,
        brigades: list[UniqueBrigade],
        active_by_brigade: dict[int, Repair],
    ) -> dict[int, list[BrigadeErrorScreen]]:
        number_by_brigade_id = {
            b.id: number
            for b in brigades
            if (number := self._extract_brigade_number(b.name)) is not None
        }
        if not number_by_brigade_id:
            return {}

        cm_brigades = await self.cm_brigade_repository.list_by_names(
            list(set(number_by_brigade_id.values())),
        )
        cm_ids_by_number: dict[str, list[int]] = defaultdict(list)
        for cm in cm_brigades:
            cm_ids_by_number[cm.name].append(cm.id)
        if not cm_ids_by_number:
            return {}

        all_cm_ids = list({cid for ids in cm_ids_by_number.values() for cid in ids})
        starts = [active_by_brigade[bid].start_time for bid in number_by_brigade_id]
        if not starts:
            return {}
        overall_start = min(starts)
        overall_end = datetime.now()  # noqa: DTZ005

        screens_repo = self.cm_brigade_error_screen_repository
        screens = await screens_repo.list_by_brigade_ids_in_range(
            all_cm_ids,
            start_time=overall_start,
            end_time=overall_end,
        )
        screens_by_cm_id: dict[int, list[BrigadeErrorScreen]] = defaultdict(list)
        for screen in screens:
            screens_by_cm_id[screen.brigade_id].append(screen)

        result: dict[int, list[BrigadeErrorScreen]] = {}
        for brigade_id, number in number_by_brigade_id.items():
            repair = active_by_brigade.get(brigade_id)
            if repair is None:
                continue
            end = repair.end_time or overall_end
            cm_ids = cm_ids_by_number.get(number, [])
            hits = [
                s
                for cm_id in cm_ids
                for s in screens_by_cm_id.get(cm_id, [])
                if repair.start_time <= s.timestamp <= end
            ]
            if hits:
                result[brigade_id] = hits
        return result

    @staticmethod
    def _extract_brigade_number(name: str) -> str | None:
        match = _BRIGADE_NUMBER_RE.search(name)
        return match.group(1) if match else None
