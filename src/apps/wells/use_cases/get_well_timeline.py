"""Хронология событий скважины: сигнал детектора → поломка → начало ремонта.

Случаи собираются на чтении из статусов ABAI, ремонтов и эпизодов
детекторов (правила — ``services/well_timeline/rules.py``); своих таблиц нет.
"""

from datetime import datetime, timedelta

from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.dto.internal.well_timeline import (
    WellTimelineBreakdownDTO,
    WellTimelineCaseDTO,
    WellTimelineDTO,
    WellTimelineRepairDTO,
    WellTimelineSignalDTO,
)
from apps.wells.dto.queries.well import GetWellTimelineQuery
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_status import WellStatusRepository
from apps.wells.services.well_timeline import config
from apps.wells.services.well_timeline.rules import (
    IncidentInput,
    RepairInput,
    StatusInput,
    TimelineCase,
    build_cases,
)
from apps.wells.use_cases.get_well_card import WellNotFoundError
from core.settings import get_settings
from shared.repository.sqlalchemy import QuerySpec


def _signal_dto(incident: IncidentInput) -> WellTimelineSignalDTO:
    return WellTimelineSignalDTO(
        at=incident.opened_at,
        incident_id=incident.incident_id,
        detector_code=incident.detector_code,
        title=incident.title,
        reason_code=incident.reason_code,
        level=incident.level,
        status=incident.status,
        verdict=incident.verdict,
    )


def _case_dto(case: TimelineCase) -> WellTimelineCaseDTO:
    signals = [_signal_dto(i) for i in case.signals]
    breakdown = case.breakdown
    repair = case.repair
    return WellTimelineCaseDTO(
        kind=case.kind,
        is_open=case.is_open,
        signal=signals[0] if signals else None,
        signals=signals,
        breakdown=(
            WellTimelineBreakdownDTO(
                at=breakdown.at,
                status_code=breakdown.code,
                title=breakdown.title,
            )
            if breakdown is not None
            else None
        ),
        repair=(
            WellTimelineRepairDTO(
                at=repair.at,
                end_at=repair.end_at,
                source=repair.source,
                repair_id=repair.repair_id,
                kind=repair.kind,
                title=repair.title,
            )
            if repair is not None
            else None
        ),
    )


class GetWellTimelineUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        well_repository: WellRepository,
        well_status_repository: WellStatusRepository,
        repair_repository: RepairRepository,
        incident_repository: DetectorIncidentRepository,
        detector_repository: DetectorRepository,
        verification_repository: DetectorVerificationRepository,
    ) -> None:
        self.well_repository = well_repository
        self.well_status_repository = well_status_repository
        self.repair_repository = repair_repository
        self.incident_repository = incident_repository
        self.detector_repository = detector_repository
        self.verification_repository = verification_repository

    async def execute(
        self,
        query: GetWellTimelineQuery,
        *,
        now: datetime | None = None,
    ) -> WellTimelineDTO:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": query.well_id})

        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        since = now - timedelta(days=query.days)
        load_since = since - config.HISTORY_DEPTH

        cases = build_cases(
            statuses=await self._statuses(well.abai_id, load_since, now),
            repairs=await self._repairs(well.abai_id, load_since),
            incidents=await self._incidents(well.id),
            since=since,
            now=now,
        )
        return WellTimelineDTO(
            well_id=well.id,
            cases=[_case_dto(case) for case in cases[: query.limit]],
        )

    async def _statuses(
        self,
        abai_well_id: int,
        since: datetime,
        now: datetime,
    ) -> list[StatusInput]:
        rows = await self.well_status_repository.list_intervals_by_abai_well_ids(
            [abai_well_id],
            since=since,
            until=now,
        )
        # Идущий статус в ABAI закрыт датой 3333-12-31.
        return [
            StatusInput(
                since=dbeg,
                until=dend if dend <= now else None,
                code=code,
                name=name,
                reason=reason,
            )
            for _, dbeg, dend, code, name, reason in rows
        ]

    async def _repairs(self, abai_well_id: int, since: datetime) -> list[RepairInput]:
        rows = await self.repair_repository.list_with_type_by_well_abai_ids(
            [abai_well_id],
            since=since,
        )
        return [
            RepairInput(
                repair_id=repair.id,
                start=repair.start_time,
                end=repair.end_time,
                repair_type_id=repair.repair_type_id,
                type_name=type_name,
                work_list=repair.work_list,
                abai_repair_type_id=repair.abai_repair_type_id,
            )
            for repair, type_name in rows
        ]

    async def _incidents(self, well_id: int) -> list[IncidentInput]:
        # Эпизодов на скважину — десятки: берём все, окно решают правила.
        incidents = await self.incident_repository.list_by_well_id(well_id=well_id)
        if not incidents:
            return []
        detectors = await self.detector_repository.get_list(QuerySpec())
        names = {detector.code: detector.name_ru for detector in detectors}
        verifications = await self.verification_repository.get_map_by_incident_ids(
            [incident.id for incident in incidents],
        )
        result = []
        for incident in incidents:
            verification = verifications.get(incident.id)
            evidence = (verification.evidence if verification else None) or {}
            result.append(
                IncidentInput(
                    incident_id=incident.id,
                    detector_code=incident.detector_code,
                    title=names.get(incident.detector_code) or incident.detector_code,
                    reason_code=incident.reason_code,
                    level=incident.level,
                    status=incident.status,
                    opened_at=incident.opened_at,
                    verdict=verification.verdict if verification else None,
                    repair_ids=frozenset(
                        item["repair_id"]
                        for item in evidence.get("repair") or []
                        if item.get("repair_id") is not None
                    ),
                ),
            )
        return result
