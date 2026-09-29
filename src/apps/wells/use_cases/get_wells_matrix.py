"""Build a matrix of wells under a given NGDU.

Resolution chain:
  1. Resolve the NGDU's wells via ``NGDUWellsService`` (org subtree → ABAI
     ``well_org`` → local ``Well``s).
  2. Mark ``is_on_repair`` when an unfinished ``Repair`` exists for the same
     ``abai_well_id``.
  3. For wells currently on repair, build a legend: the active repair,
     the unique brigade linked to it (nullable), and — if a brigade is
     found — the danger-zone violation screens accumulated during that
     repair's interval.
"""

from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from apps.org.dto.internal.brigade import BrigadeDangerDTO, BrigadeShortDTO
from apps.repairs.dto.internal.repair import RepairDTO
from apps.wells.dto.internal.well_matrix import WellLegendDTO, WellMatrixItemDTO
from apps.wells.services.well_status import WellStatusService
from core.settings import get_settings

_YEAR = timedelta(days=365)

if TYPE_CHECKING:
    from apps.org.models.brigade import UniqueBrigade
    from apps.org.repositories.brigade import UniqueBrigadeRepository
    from apps.repairs.models.repair import Repair
    from apps.repairs.repositories.brigade import RepairBrigadeRepository
    from apps.repairs.repositories.repair import RepairRepository
    from apps.wells.dto.queries.well import GetWellsMatrixQuery
    from apps.wells.models.well import Well
    from apps.wells.repositories.spo import SPORepository
    from apps.wells.services import NGDUWellsService
    from shared.integrations.cm.models import BrigadeErrorScreen
    from shared.integrations.cm.repositories.brigade_error_screens import (
        CMBrigadeErrorScreenRepository,
    )
    from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

DANGER_TYPE_VIOLATION = "violation"

_BRIGADE_NUMBER_RE = re.compile(r"№\s*(\d+)")


class GetWellsMatrixUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        ngdu_wells_service: NGDUWellsService,
        repair_repository: RepairRepository,
        repair_brigade_repository: RepairBrigadeRepository,
        unique_brigade_repository: UniqueBrigadeRepository,
        cm_brigade_repository: CMBrigadeRepository,
        cm_brigade_error_screen_repository: CMBrigadeErrorScreenRepository,
        spo_repository: SPORepository,
    ) -> None:
        self.ngdu_wells_service = ngdu_wells_service
        self.well_status_service = WellStatusService(spo_repository)
        self.repair_repository = repair_repository
        self.repair_brigade_repository = repair_brigade_repository
        self.unique_brigade_repository = unique_brigade_repository
        self.cm_brigade_repository = cm_brigade_repository
        self.cm_brigade_error_screen_repository = cm_brigade_error_screen_repository

    async def execute(
        self,
        query: GetWellsMatrixQuery,
    ) -> list[WellMatrixItemDTO]:
        wells = await self.ngdu_wells_service.list_wells(query.ngdu_id)
        if not wells:
            return []

        active_repairs = await self.repair_repository.list_active_by_well_abai_ids(
            [w.abai_id for w in wells],
        )
        # ``list_active_by_well_abai_ids`` orders by ``start_time DESC`` so the
        # first repair per well is the most recent active one — if there are
        # ever duplicates, the latter (older) entry gets overwritten.
        active_repair_by_abai_well: dict[int, Repair] = {}
        for r in active_repairs:
            active_repair_by_abai_well.setdefault(r.abai_well_id, r)

        brigade_by_repair_id = await self._load_brigade_by_repair_id(
            list(active_repair_by_abai_well.values()),
        )
        dangers_by_brigade_id = await self._load_dangers_by_brigade_id(
            brigade_by_repair_id,
            active_repair_by_abai_well,
        )
        frequent_repair_abai_well_ids = await self._frequent_repair_abai_well_ids(
            [w.abai_id for w in wells],
        )
        work_codes = await self.well_status_service.work_codes(
            {
                w.id: active_repair_by_abai_well[w.abai_id]
                for w in wells
                if w.abai_id in active_repair_by_abai_well
            },
        )

        return [
            self._build_item(
                well=w,
                active_repair=active_repair_by_abai_well.get(w.abai_id),
                brigade_by_repair_id=brigade_by_repair_id,
                dangers_by_brigade_id=dangers_by_brigade_id,
                is_frequent_repair=w.abai_id in frequent_repair_abai_well_ids,
                work_code=work_codes.get(w.id),
            )
            for w in sorted(wells, key=lambda w: w.name)
        ]

    async def _frequent_repair_abai_well_ids(
        self,
        abai_well_ids: list[int],
    ) -> set[int]:
        if not abai_well_ids:
            return set()
        threshold = get_settings().FREQUENT_REPAIR_THRESHOLD
        since = datetime.now() - _YEAR  # noqa: DTZ005
        counts = await self.repair_repository.count_by_well_abai_ids_since(
            abai_well_ids,
            since=since,
        )
        return {abai_id for abai_id, count in counts.items() if count > threshold}

    def _build_item(  # noqa: PLR0913
        self,
        *,
        well: Well,
        active_repair: Repair | None,
        brigade_by_repair_id: dict[int, UniqueBrigade],
        dangers_by_brigade_id: dict[int, list[BrigadeErrorScreen]],
        is_frequent_repair: bool,
        work_code: int | None,
    ) -> WellMatrixItemDTO:
        status = WellStatusService.status(
            active_repair=active_repair,
            work_code=work_code,
        )
        if active_repair is None:
            return WellMatrixItemDTO(
                id=well.id,
                name=well.name,
                is_on_repair=False,
                is_frequent_repair=is_frequent_repair,
                status=status,
            )
        brigade = brigade_by_repair_id.get(active_repair.id)
        brigade_dto = (
            BrigadeShortDTO.model_validate(brigade) if brigade is not None else None
        )
        screens = (
            dangers_by_brigade_id.get(brigade.id, []) if brigade is not None else []
        )
        dangers = [
            BrigadeDangerDTO(
                type=DANGER_TYPE_VIOLATION,
                time=s.timestamp,
                description=s.description or "",
            )
            for s in sorted(screens, key=lambda s: s.timestamp, reverse=True)
        ]
        return WellMatrixItemDTO(
            id=well.id,
            name=well.name,
            is_on_repair=True,
            repair_id=active_repair.id,
            is_frequent_repair=is_frequent_repair,
            status=status,
            legend=WellLegendDTO(
                repair=RepairDTO.model_validate(active_repair),
                brigade=brigade_dto,
                dangers=dangers,
            ),
        )

    async def _load_brigade_by_repair_id(
        self,
        active_repairs: list[Repair],
    ) -> dict[int, UniqueBrigade]:
        if not active_repairs:
            return {}
        repair_ids = [r.id for r in active_repairs]
        links = await self.repair_brigade_repository.list_by_repair_ids(repair_ids)
        if not links:
            return {}
        # ``repair_id`` is unique on RepairBrigade, so at most one link per repair.
        brigade_ids = list({link.brigade_id for link in links})
        result: dict[int, UniqueBrigade] = {}
        for bid in brigade_ids:
            brigade = await self.unique_brigade_repository.get_by_id(bid)
            if brigade is None:
                continue
            for link in links:
                if link.brigade_id == bid:
                    result[link.repair_id] = brigade
        return result

    async def _load_dangers_by_brigade_id(
        self,
        brigade_by_repair_id: dict[int, UniqueBrigade],
        active_repair_by_abai_well: dict[int, Repair],
    ) -> dict[int, list[BrigadeErrorScreen]]:
        if not brigade_by_repair_id:
            return {}
        repair_by_brigade_id = self._repair_by_brigade_id(
            brigade_by_repair_id,
            active_repair_by_abai_well,
        )
        number_by_brigade_id = {
            b.id: number
            for b in brigade_by_repair_id.values()
            if (number := self._extract_brigade_number(b.name)) is not None
        }
        if not number_by_brigade_id:
            return {}

        cm_ids_by_number = await self._load_cm_ids_by_number(
            set(number_by_brigade_id.values()),
        )
        if not cm_ids_by_number:
            return {}

        overall_start = min(r.start_time for r in repair_by_brigade_id.values())
        overall_end = datetime.now()  # noqa: DTZ005
        screens_by_cm_id = await self._load_screens_by_cm_id(
            cm_ids_by_number,
            overall_start=overall_start,
            overall_end=overall_end,
        )

        result: dict[int, list[BrigadeErrorScreen]] = {}
        for brigade_id, number in number_by_brigade_id.items():
            repair = repair_by_brigade_id.get(brigade_id)
            if repair is None:
                continue
            end = repair.end_time or overall_end
            hits = [
                s
                for cm_id in cm_ids_by_number.get(number, [])
                for s in screens_by_cm_id.get(cm_id, [])
                if repair.start_time <= s.timestamp <= end
            ]
            if hits:
                result[brigade_id] = hits
        return result

    @staticmethod
    def _repair_by_brigade_id(
        brigade_by_repair_id: dict[int, UniqueBrigade],
        active_repair_by_abai_well: dict[int, Repair],
    ) -> dict[int, Repair]:
        repair_by_id = {r.id: r for r in active_repair_by_abai_well.values()}
        result: dict[int, Repair] = {}
        for repair_id, brigade in brigade_by_repair_id.items():
            repair = repair_by_id.get(repair_id)
            if repair is not None:
                result[brigade.id] = repair
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
        *,
        overall_start: datetime,
        overall_end: datetime,
    ) -> dict[int, list[BrigadeErrorScreen]]:
        all_cm_ids = list({cid for ids in cm_ids_by_number.values() for cid in ids})
        screens = await (
            self.cm_brigade_error_screen_repository.list_by_brigade_ids_in_range(
                all_cm_ids,
                start_time=overall_start,
                end_time=overall_end,
            )
        )
        result: dict[int, list[BrigadeErrorScreen]] = defaultdict(list)
        for s in screens:
            result[s.brigade_id].append(s)
        return result

    @staticmethod
    def _extract_brigade_number(name: str) -> str | None:
        match = _BRIGADE_NUMBER_RE.search(name)
        return match.group(1) if match else None
