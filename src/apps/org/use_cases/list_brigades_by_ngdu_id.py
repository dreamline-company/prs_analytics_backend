"""List ``org_unique_brigade`` rows for an NGDU with derived per-brigade
metrics — ``is_in_repair`` and ``violations_count`` — computed against
``repairs_repair_brigade`` and CM ``BrigadeErrorScreen``.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import (
    BrigadeDangerDTO,
    BrigadeDTO,
    BrigadeLegendDTO,
)
from apps.repairs.dto.internal.repair import RepairDTO
from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.services.well_status import WellStatusService
from core.settings import get_settings

_YEAR = timedelta(days=365)

if TYPE_CHECKING:
    from apps.org.dto.queries.brigade import ListBrigadesByNGDUIdQuery
    from apps.org.models.brigade import UniqueBrigade
    from apps.org.repositories import UniqueBrigadeRepository
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from apps.wells.models.well import Well
    from apps.wells.repositories import WellRepository
    from apps.wells.repositories.spo import SPORepository
    from shared.integrations.cm.models import BrigadeErrorScreen
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

DANGER_TYPE_VIOLATION = "violation"

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class ListBrigadesByNGDUIdUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        unique_brigade_repository: UniqueBrigadeRepository,
        repair_brigade_repository: RepairBrigadeRepository,
        repair_repository: RepairRepository,
        well_repository: WellRepository,
        cm_brigade_repository: CMBrigadeRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
        spo_repository: SPORepository,
    ) -> None:
        self.well_status_service = WellStatusService(spo_repository)
        self.unique_brigade_repository = unique_brigade_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.repair_repository = repair_repository
        self.well_repository = well_repository
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
        # Pick the most recently started open repair per brigade. If somehow
        # more than one exists concurrently, the latest start wins.
        active_repair_by_brigade: dict[int, Repair] = {}
        for bid, repairs in brigade_repairs.items():
            active = [r for r in repairs if r.end_time is None]
            if not active:
                continue
            active.sort(key=lambda r: r.start_time, reverse=True)
            active_repair_by_brigade[bid] = active[0]
        (
            violations_by_brigade,
            active_dangers_by_brigade,
        ) = await self._compute_violations(
            brigades,
            brigade_repairs,
            active_repair_by_brigade,
        )
        wells_by_repair_id = await self._load_wells_for_active_repairs(
            active_repair_by_brigade,
        )
        frequent_repair_abai_well_ids = await self._frequent_repair_abai_well_ids(
            active_repair_by_brigade,
        )
        live_spo_well_ids = await self.well_status_service.live_spo_well_ids(
            w.id for w in wells_by_repair_id.values()
        )

        result: list[BrigadeDTO] = []
        for brigade in brigades:
            dto = BrigadeDTO.model_validate(brigade)
            active_repair = active_repair_by_brigade.get(brigade.id)
            dto.is_in_repair = active_repair is not None
            dto.repair_id = active_repair.id if active_repair is not None else None
            dto.violations_count = violations_by_brigade.get(brigade.id, 0)
            if active_repair is not None:
                dto.is_frequent_repair = (
                    active_repair.abai_well_id in frequent_repair_abai_well_ids
                )
                well = wells_by_repair_id.get(active_repair.id)
                dto.status = WellStatusService.status(
                    is_spo_live=well is not None and well.id in live_spo_well_ids,
                    active_repair=active_repair,
                )
                if well is not None:
                    screens = active_dangers_by_brigade.get(brigade.id, [])
                    dangers = [
                        BrigadeDangerDTO(
                            type=DANGER_TYPE_VIOLATION,
                            time=s.timestamp,
                            description=s.description or "",
                        )
                        for s in sorted(
                            screens,
                            key=lambda s: s.timestamp,
                            reverse=True,
                        )
                    ]
                    dto.legend = BrigadeLegendDTO(
                        well=WellShortDTO.model_validate(well),
                        repair=RepairDTO.model_validate(active_repair),
                        dangers=dangers,
                        status=dto.status,
                    )
            result.append(dto)
        return result

    async def _frequent_repair_abai_well_ids(
        self,
        active_repair_by_brigade: dict[int, Repair],
    ) -> set[int]:
        abai_well_ids = list(
            {
                r.abai_well_id
                for r in active_repair_by_brigade.values()
                if r.abai_well_id is not None
            },
        )
        if not abai_well_ids:
            return set()
        threshold = get_settings().FREQUENT_REPAIR_THRESHOLD
        since = datetime.now() - _YEAR  # noqa: DTZ005
        counts = await self.repair_repository.count_by_well_abai_ids_since(
            abai_well_ids,
            since=since,
        )
        return {abai_id for abai_id, count in counts.items() if count > threshold}

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

    async def _compute_violations(
        self,
        brigades: list[UniqueBrigade],
        brigade_repairs: dict[int, list[Repair]],
        active_repair_by_brigade: dict[int, Repair],
    ) -> tuple[dict[int, int], dict[int, list[BrigadeErrorScreen]]]:
        number_by_brigade_id = {
            b.id: number
            for b in brigades
            if b.id in brigade_repairs
            and (number := self._extract_brigade_number(b.name)) is not None
        }
        if not number_by_brigade_id:
            return {}, {}

        cm_ids_by_number = await self._load_cm_ids_by_number(
            set(number_by_brigade_id.values()),
        )
        if not cm_ids_by_number:
            return {}, {}

        screens_by_cm_id = await self._load_screens_by_cm_id(
            cm_ids_by_number,
            brigade_repairs,
        )
        if not screens_by_cm_id:
            return {}, {}

        counts: dict[int, int] = {}
        active_dangers: dict[int, list[BrigadeErrorScreen]] = {}
        for brigade_id, repairs in brigade_repairs.items():
            number = number_by_brigade_id.get(brigade_id)
            if number is None:
                continue
            cm_ids = cm_ids_by_number.get(number, [])
            candidate_screens = [
                s for cm_id in cm_ids for s in screens_by_cm_id.get(cm_id, [])
            ]
            hits = [
                screen
                for screen in candidate_screens
                if self._within_any_repair(screen.timestamp, repairs)
            ]
            if hits:
                counts[brigade_id] = len(hits)
            active_repair = active_repair_by_brigade.get(brigade_id)
            if active_repair is not None:
                end = active_repair.end_time or datetime.now()  # noqa: DTZ005
                active_hits = [
                    s
                    for s in candidate_screens
                    if active_repair.start_time <= s.timestamp <= end
                ]
                if active_hits:
                    active_dangers[brigade_id] = active_hits
        return counts, active_dangers

    async def _load_wells_for_active_repairs(
        self,
        active_repair_by_brigade: dict[int, Repair],
    ) -> dict[int, Well]:
        if not active_repair_by_brigade:
            return {}
        abai_well_ids = {
            r.abai_well_id
            for r in active_repair_by_brigade.values()
            if r.abai_well_id is not None
        }
        wells_by_abai_id: dict[int, Well] = {}
        if abai_well_ids:
            wells = await self.well_repository.list_by_abai_ids(list(abai_well_ids))
            wells_by_abai_id = {w.abai_id: w for w in wells}

        result: dict[int, Well] = {}
        for repair in active_repair_by_brigade.values():
            well = (
                wells_by_abai_id.get(repair.abai_well_id)
                if repair.abai_well_id is not None
                else None
            )
            if well is None and repair.well_id is not None:
                well = await self.well_repository.get_by_id(id_=repair.well_id)
            if well is not None:
                result[repair.id] = well
        return result

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
