"""Прогон итогового состояния скважин по всем НГДУ.

1. Скважины всех НГДУ — один раз в начале прогона.
2. СДМО — по каждому НГДУ со станциями, своя транзакция: новые отсчёты
   каждой станции после её последнего виденного (``last_seen_at``) → автомат
   привода (``wells_drive_state``). Станция без автомата (первый прогон,
   новая, перепривязанная) — поиск начала текущего состояния назад по истории
   не глубже 30 суток. Сбой СДМО по НГДУ — за 20 минут отчиталось меньше
   половины станций, отчитывавшихся за 7 суток. СДМО идёт до скважин, чтобы
   станция одного НГДУ, привязанная к скважине другого, не теряла смены.
3. Скважины — по каждому НГДУ, своя транзакция: статус ABAI, идущий ремонт,
   эпизоды детекторов, привод → ``rules.decide`` → ``plan_transition`` →
   ``wells_well_state``; смены привода внутри прогона пишутся все, по
   времени. Строка прогона ``wells_state_run``.
4. Открытые строки скважин, выпавших из всех НГДУ, закрываются.

Ошибка одного НГДУ логируется и не обрывает остальные; лимит времени таски
(``SoftTimeLimitExceeded``) прерывает прогон целиком. Все времена — местные
наивные (СДМО, ABAI и ремонты пишутся в местном).
"""

import time
from collections import Counter, defaultdict
from collections.abc import Coroutine, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Any

from celery.exceptions import SoftTimeLimitExceeded
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.well_status import WellIncidentStatusDTO
from apps.detectors.models.incident import INCIDENT_LEVEL_ALARM
from apps.detectors.repositories import DetectorIncidentRepository, DetectorRepository
from apps.detectors.services import WellIncidentStatusService
from apps.detectors.services.well_incident_status import WELL_STATE_NORMAL
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.wells.dto.internal.repositories.well_state import (
    CreateWellStateDTO,
    UpdateWellStateDTO,
    UpsertWellDriveStateDTO,
    UpsertWellStateRunDTO,
)
from apps.wells.models.well_state import WellDriveState, WellState
from apps.wells.repositories import (
    WellDriveStateRepository,
    WellOrgRepository,
    WellRepository,
    WellStateRepository,
    WellStateRunRepository,
    WellStatusRepository,
)
from apps.wells.services.ngdu_wells import NGDUWellsService
from apps.wells.services.well_state.drive import (
    NO_LINK_GAP,
    DriveChange,
    DriveSample,
    DriveSnapshot,
    DriveStats,
    HistoryScan,
    advance,
)
from apps.wells.services.well_state.rules import (
    ABAI_KNOWN_CODES,
    KIND_IDLE,
    STATION_LOST_AFTER,
    AbaiStatusInput,
    Decision,
    DriveInput,
    IncidentInput,
    RepairInput,
    SdmoInput,
    WellInputs,
    decide,
)
from apps.wells.services.well_state.transitions import (
    KIND_NONE,
    KIND_OPEN,
    KIND_REPLACE,
    KIND_REWRITE,
    KIND_UPDATE,
    OpenState,
    plan_transition,
)
from core import get_logger
from shared.constants.ngdu import NGDU_ORG_TYPE

logger = get_logger(__name__)

# Сбой СДМО по НГДУ: окно короче порога «нет связи» (60 минут), чтобы сбой
# опознавался раньше, чем станции уйдут в «нет связи». База — станции,
# отчитывавшиеся за 7 суток (как «станция снята»): после суток сбоя база с
# окном в сутки опустела бы, и все скважины разом ушли бы в «нет связи».
OUTAGE_WINDOW = timedelta(minutes=20)
OUTAGE_BASE_WINDOW = STATION_LOST_AFTER
# Новых отсчётов станции за прогон — не больше (ЖМГ пишет раз в минуту: это
# ~3,5 суток); остальное — следующим прогоном, курсор станции сохраняется.
ROWS_PER_STATION = 5_000
# Отсчёт «из будущего» (часы станции спешат) не принимается.
FUTURE_TOLERANCE = timedelta(minutes=10)
# Поиск начала текущего состояния привода по истории станции.
INIT_LOOKBACK = timedelta(days=30)
INIT_CHUNK = 2_000


@dataclass(frozen=True, slots=True)
class StationRef:
    """Снимок станции: ORM-объекты после rollback протухают."""

    id: int
    well_id: int | None
    abai_ngdu_id: int
    active: bool


@dataclass(frozen=True, slots=True)
class WellRef:
    """Снимок скважины: ORM-объекты после rollback протухают (dry-run, сбой
    одного НГДУ), а список скважин берётся один раз на весь прогон."""

    id: int
    abai_id: int


@dataclass(slots=True)
class NgduResult:
    abai_ngdu_id: int
    wells: int = 0
    states: Counter = field(default_factory=Counter)
    transitions: Counter = field(default_factory=Counter)
    fc_rows: int = 0
    initialized_stations: int = 0
    outage_since: datetime | None = None
    error: str | None = None


# --- Чистые помощники (без базы) ---


def current_statuses(
    intervals: Iterable[tuple],
    now: datetime,
) -> dict[int, AbaiStatusInput]:
    """Статус ABAI на ``now``: интервал, покрывающий момент, с наибольшим
    началом (открытые интервалы перечитываются раз в час, и старый может быть
    ещё не закрыт)."""
    current: dict[int, AbaiStatusInput] = {}
    for abai_well_id, dbeg, dend, code, name, reason in intervals:
        if not dbeg <= now < dend:
            continue
        known = current.get(abai_well_id)
        if known is None or dbeg >= known.since:
            current[abai_well_id] = AbaiStatusInput(code, name, reason, dbeg)
    return current


def active_repairs(
    repairs: Mapping[int, CurrentRepairDTO],
    intervals: Iterable[tuple],
) -> dict[int, RepairInput]:
    """Идущие ремонты: открыт в ABAI и после начала не начинался статус,
    кроме простоя (WRK, периодика, консервация…).

    Ремонты из ABAI приходят раз в сутки (``repairs.sync``), статусы — каждый
    час: без этой проверки закрытый днём ремонт висел бы до утра, а висящий с
    2024 года — всегда.
    """
    ended = {
        abai_well_id
        for abai_well_id, dbeg, _dend, code, name, _reason in intervals
        if abai_well_id in repairs
        and dbeg > repairs[abai_well_id].start_time
        and AbaiStatusInput(code, name, None, dbeg).kind != KIND_IDLE
    }
    return {
        abai_well_id: RepairInput(
            repair_id=repair.id,
            type_name=repair.repair_type_name_ru,
            since=repair.start_time,
        )
        for abai_well_id, repair in repairs.items()
        if abai_well_id not in ended
    }


def detect_outage(last_seen: Iterable[datetime | None], now: datetime) -> bool:
    """Сбой СДМО по НГДУ: из станций, отчитывавшихся за 7 суток, за последние
    20 минут отчиталось меньше половины."""
    base = [
        moment
        for moment in last_seen
        if moment is not None and moment >= now - OUTAGE_BASE_WINDOW
    ]
    if not base:
        return False
    reporting = sum(1 for moment in base if moment >= now - OUTAGE_WINDOW)
    return reporting * 2 < len(base)


def incident_input(status: WellIncidentStatusDTO | None) -> IncidentInput | None:
    """Худший активный эпизод; для эскалированной аварии начало — эскалация."""
    if status is None or status.level == WELL_STATE_NORMAL or not status.incidents:
        return None
    worst = status.incidents[0]
    since = worst.opened_at
    if status.level == INCIDENT_LEVEL_ALARM and worst.escalated_at is not None:
        since = worst.escalated_at
    return IncidentInput(
        level=status.level,
        title=status.title_ru,
        since=since,
        incident_id=worst.id,
    )


def _snapshot_of(row: WellDriveState) -> DriveSnapshot:
    return DriveSnapshot(
        running=row.running,
        since=row.since,
        pending_running=row.pending_running,
        pending_since=row.pending_since,
        reconfirming=row.reconfirming,
        last_sample_at=row.last_sample_at,
        last_seen_at=row.last_seen_at,
    )


def _open_of(row: WellState) -> OpenState:
    return OpenState(
        id=row.id,
        state=row.state,
        source=row.source,
        reason_code=row.reason_code,
        reason_ru=row.reason_ru,
        started_at=row.started_at,
        reason_detail_ru=row.reason_detail_ru,
    )


class WellStateRefresher:
    def __init__(self, session: AsyncSession, *, now: datetime) -> None:
        """``now`` — наивное местное время прогона."""
        self.session = session
        self.now = now
        self.org_repository = OrgRepository(session)
        self.ngdu_wells_service = NGDUWellsService(
            org_repository=self.org_repository,
            well_repository=WellRepository(session),
            well_org_repository=WellOrgRepository(session),
            oil_field_repository=OilFieldRepository(session),
        )
        self.status_repository = WellStatusRepository(session)
        self.repair_repository = RepairRepository(session)
        self.incident_status_service = WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session),
            detector_repository=DetectorRepository(session),
        )
        self.station_repository = SdmoStationRepository(session)
        self.fc_data_repository = SdmoFcDataRepository(session)
        self.state_repository = WellStateRepository(session)
        self.drive_repository = WellDriveStateRepository(session)
        self.run_repository = WellStateRunRepository(session)

        # Состояние прогона в памяти: переживает rollback (dry-run) и
        # переходит между НГДУ (станция может принадлежать другому НГДУ).
        self.snapshots: dict[int, DriveSnapshot] = {}
        self.stations: dict[int, StationRef] = {}
        self.changes: dict[int, list[DriveChange]] = defaultdict(list)
        self.outage_since_by_ngdu: dict[int, datetime | None] = {}
        self.drive_stats = DriveStats()
        self.unknown_abai_codes: Counter = Counter()
        self.future_samples = 0

    async def run(self, *, dry_run: bool = False) -> list[NgduResult]:
        orgs = await self.org_repository.list_all()
        # Снимки до цикла: после commit/rollback ORM-объекты протухают.
        ngdus = sorted(
            (org.id, org.abai_id) for org in orgs if org.org_type_id == NGDU_ORG_TYPE
        )
        self.outage_since_by_ngdu = {
            ngdu_id: run.sdmo_outage_since
            for ngdu_id, run in (await self.run_repository.get_map()).items()
        }
        wells_by_ngdu: dict[int, list[WellRef]] = {}
        seen: set[int] = set()
        for org_id, abai_ngdu_id in ngdus:
            wells = [
                WellRef(id=well.id, abai_id=well.abai_id)
                for well in await self.ngdu_wells_service.list_wells(org_id)
                if well.id not in seen
            ]
            seen.update(well.id for well in wells)
            wells_by_ngdu[abai_ngdu_id] = wells
        results = {abai_ngdu_id: NgduResult(abai_ngdu_id) for _, abai_ngdu_id in ngdus}
        started = {abai_ngdu_id: time.monotonic() for abai_ngdu_id in results}

        for result in results.values():
            await self._in_transaction(
                result,
                self._refresh_sdmo(result),
                dry_run=dry_run,
            )
        for abai_ngdu_id, result in results.items():
            await self._in_transaction(
                result,
                self._refresh_ngdu_wells(
                    result,
                    wells_by_ngdu[abai_ngdu_id],
                    started=started[abai_ngdu_id],
                ),
                dry_run=dry_run,
            )
        await self._in_transaction(None, self._close_dropped(seen), dry_run=dry_run)
        self._log(results.values())
        return list(results.values())

    def _log(self, results: Iterable[NgduResult]) -> None:
        for result in results:
            logger.info(
                "Well state ngdu=%s: wells=%s states=%s transitions=%s fc_rows=%s "
                "init_stations=%s outage_since=%s error=%s",
                result.abai_ngdu_id,
                result.wells,
                dict(result.states),
                dict(result.transitions),
                result.fc_rows,
                result.initialized_stations,
                result.outage_since,
                result.error,
            )
        if self.drive_stats.out_of_order or self.future_samples:
            logger.info(
                "Well state drive: out_of_order=%s future=%s",
                self.drive_stats.out_of_order,
                self.future_samples,
            )
        if self.drive_stats.unknown_vlt:
            logger.warning(
                "Well state: unknown VLT codes %s",
                dict(self.drive_stats.unknown_vlt),
            )
        if self.unknown_abai_codes:
            logger.warning(
                "Well state: unknown ABAI status codes %s",
                dict(self.unknown_abai_codes),
            )

    async def _in_transaction(
        self,
        result: NgduResult | None,
        work: Coroutine[Any, Any, None],
        *,
        dry_run: bool,
    ) -> None:
        try:
            await work
            if dry_run:
                await self.session.rollback()
            else:
                await self.session.commit()
        except SoftTimeLimitExceeded:
            await self.session.rollback()
            raise
        except Exception as error:
            await self.session.rollback()
            if result is not None:
                result.error = repr(error)
            logger.exception(
                "Well state ngdu=%s failed",
                result.abai_ngdu_id if result is not None else None,
            )

    # --- СДМО ---

    async def _load_stations(self, rows: Sequence) -> list[StationRef]:
        """Запомнить станции и подтянуть их автоматы из базы. Автомат другой
        скважины (станцию перепривязали) не берётся — станция пойдёт на поиск
        по истории как новая."""
        refs = [
            StationRef(
                id=row.id,
                well_id=row.well_id,
                abai_ngdu_id=row.abai_ngdu_id,
                active=row.active is not False,
            )
            for row in rows
        ]
        missing = [ref.id for ref in refs if ref.id not in self.snapshots]
        stored = await self.drive_repository.get_map_by_station_ids(missing)
        for ref in refs:
            self.stations[ref.id] = ref
            row = stored.get(ref.id)
            if (
                ref.id not in self.snapshots
                and row is not None
                and row.well_id == ref.well_id
            ):
                self.snapshots[ref.id] = _snapshot_of(row)
        return refs

    async def _refresh_sdmo(self, result: NgduResult) -> None:
        abai_ngdu_id = result.abai_ngdu_id
        stations = [
            station
            for station in await self._load_stations(
                await self.station_repository.list_by_ngdu(abai_ngdu_id),
            )
            if station.active and station.well_id is not None
        ]
        if not stations:
            self.outage_since_by_ngdu[abai_ngdu_id] = None
            return
        for station in stations:
            if station.id not in self.snapshots:
                self.snapshots[station.id] = await self._init_station(station.id)
                result.initialized_stations += 1

        rows = await self.fc_data_repository.list_new_rows_by_stations(
            {
                station.id: self.snapshots[station.id].last_seen_at
                or self.now - NO_LINK_GAP
                for station in stations
            },
            limit_per_station=ROWS_PER_STATION,
        )
        result.fc_rows = len(rows)
        samples: dict[int, list[DriveSample]] = defaultdict(list)
        for row in rows:
            if row["savetime"] > self.now + FUTURE_TOLERANCE:
                self.future_samples += 1
                continue
            samples[row["station_id"]].append(
                DriveSample(row["savetime"], row["r_1999"], row["r_1998"]),
            )
        for station_id, station_samples in samples.items():
            self.changes[station_id].extend(
                advance(self.snapshots[station_id], station_samples, self.drive_stats),
            )

        outage = detect_outage(
            (self.snapshots[station.id].last_seen_at for station in stations),
            self.now,
        )
        previous = self.outage_since_by_ngdu.get(abai_ngdu_id)
        outage_since = (previous or self.now - OUTAGE_WINDOW) if outage else None
        self.outage_since_by_ngdu[abai_ngdu_id] = outage_since
        result.outage_since = outage_since
        await self._save_snapshots(stations)

    async def _init_station(self, station_id: int) -> DriveSnapshot:
        """Порции отсчётов назад, пока не найдётся последняя подтверждённая
        смена привода или не кончится глубина поиска (``HistoryScan``)."""
        scan = HistoryScan()
        before = None
        while True:
            rows = await self.fc_data_repository.list_station_rows_before(
                station_id,
                before=before,
                since=self.now - INIT_LOOKBACK,
                limit=INIT_CHUNK,
            )
            if not rows:
                break
            found = scan.feed(
                [
                    DriveSample(row["savetime"], row["r_1999"], row["r_1998"])
                    for row in rows
                    if row["savetime"] <= self.now + FUTURE_TOLERANCE
                ],
            )
            before = rows[-1]["savetime"]
            # Без пригодных отсчётов в последней порции (станция без статуса и
            # оборотов) глубже не смотрим.
            if found or len(rows) < INIT_CHUNK or scan.target is None:
                break
        return scan.snapshot()

    async def _save_snapshots(self, stations: Sequence[StationRef]) -> None:
        await self.drive_repository.upsert_many(
            [
                UpsertWellDriveStateDTO(
                    station_id=station.id,
                    well_id=station.well_id,
                    running=snapshot.running,
                    since=snapshot.since,
                    pending_running=snapshot.pending_running,
                    pending_since=snapshot.pending_since,
                    reconfirming=snapshot.reconfirming,
                    last_sample_at=snapshot.last_sample_at,
                    last_seen_at=snapshot.last_seen_at,
                    updated_at=self.now,
                )
                for station in stations
                if (snapshot := self.snapshots.get(station.id)) is not None
            ],
        )

    # --- Скважины ---

    async def _refresh_ngdu_wells(
        self,
        result: NgduResult,
        wells: Sequence[WellRef],
        *,
        started: float,
    ) -> None:
        result.wells = len(wells)
        if wells:
            await self._refresh_wells(wells, result)
        await self.run_repository.upsert(
            UpsertWellStateRunDTO(
                abai_ngdu_id=result.abai_ngdu_id,
                last_run_at=self.now,
                sdmo_outage_since=self.outage_since_by_ngdu.get(result.abai_ngdu_id),
                last_duration_ms=round((time.monotonic() - started) * 1000),
            ),
        )

    async def _refresh_wells(
        self,
        wells: Sequence[WellRef],
        result: NgduResult,
    ) -> None:
        inputs = await self._collect_inputs(wells)
        open_rows = await self.state_repository.list_open_by_well_ids(
            [well.id for well in wells],
        )
        moved = {
            well_id
            for well_id, row in open_rows.items()
            if row.abai_ngdu_id != result.abai_ngdu_id
        }
        open_by_well = {well_id: _open_of(row) for well_id, row in open_rows.items()}
        for well in wells:
            well_inputs, primary = inputs[well.id]
            open_state = open_by_well.get(well.id)
            # Смены привода внутри прогона — по времени, новее открытой строки.
            for change in self.changes.get(primary, ()) if primary else ():
                at = min(change.at, self.now)
                if open_state is None or at <= open_state.started_at:
                    continue
                drive = replace(
                    well_inputs.sdmo.drive,
                    running=change.running,
                    since=at,
                    known=True,
                    last_sample_at=at,
                )
                moment_inputs = replace(
                    well_inputs,
                    sdmo=replace(well_inputs.sdmo, drive=drive),
                )
                open_state = await self._apply(
                    well.id,
                    result,
                    open_state,
                    decide(moment_inputs, at),
                    at,
                )
            decision = decide(well_inputs, self.now)
            await self._apply(
                well.id,
                result,
                open_state,
                decision,
                self.now,
                force=well.id in moved,
            )
            result.states[decision.state] += 1

    async def _close_dropped(self, current: set[int]) -> None:
        """Скважины, выпавшие из всех НГДУ (удалены, переведены), — открытая
        строка закрывается."""
        closed = 0
        for row_id, well_id, _ngdu in await self.state_repository.list_open_refs():
            if well_id in current:
                continue
            await self.state_repository.update_by_id(
                row_id,
                UpdateWellStateDTO(ended_at=self.now, updated_at=self.now),
            )
            closed += 1
        if closed:
            logger.info("Well state: closed %s rows of dropped wells", closed)

    async def _apply(  # noqa: PLR0913
        self,
        well_id: int,
        result: NgduResult,
        open_state: OpenState | None,
        decision: Decision,
        at: datetime,
        *,
        force: bool = False,
    ) -> OpenState | None:
        """``force`` — переписать строку и без смены решения (скважина
        перешла в другой НГДУ: строка должна принадлежать ему)."""
        transition = plan_transition(open_state, decision, at)
        kind = transition.kind
        if kind == KIND_NONE and force:
            kind = KIND_UPDATE
        if kind == KIND_NONE:
            return open_state
        result.transitions[kind] += 1
        fields = {
            "abai_ngdu_id": result.abai_ngdu_id,
            "state": decision.state,
            "source": decision.source,
            "reason_code": decision.reason_code,
            "reason_ru": decision.reason_ru,
            "reason_detail_ru": decision.reason_detail_ru,
            "evidence": decision.evidence,
            "updated_at": self.now,
        }
        if kind in (KIND_UPDATE, KIND_REWRITE):
            await self.state_repository.update_by_id(
                open_state.id,
                UpdateWellStateDTO(**fields),
            )
            return replace(
                open_state,
                state=decision.state,
                source=decision.source,
                reason_code=decision.reason_code,
                reason_ru=decision.reason_ru,
                reason_detail_ru=decision.reason_detail_ru,
            )
        if kind == KIND_REPLACE:
            await self.state_repository.update_by_id(
                open_state.id,
                UpdateWellStateDTO(ended_at=transition.started_at, updated_at=self.now),
            )
        if kind in (KIND_REPLACE, KIND_OPEN):
            row = await self.state_repository.create(
                CreateWellStateDTO(
                    well_id=well_id,
                    started_at=transition.started_at,
                    **fields,
                ),
            )
            return _open_of(row)
        return open_state

    async def _collect_inputs(
        self,
        wells: Sequence[WellRef],
    ) -> dict[int, tuple[WellInputs, int | None]]:
        """Входы правил на скважину и станция, по которой взят привод."""
        abai_ids = [well.abai_id for well in wells]
        well_ids = [well.id for well in wells]
        statuses = current_statuses(
            await self.status_repository.list_intervals_by_abai_well_ids(
                abai_ids,
                since=self.now,
                until=self.now + timedelta(seconds=1),
            ),
            self.now,
        )
        for status in statuses.values():
            if status.code not in ABAI_KNOWN_CODES:
                self.unknown_abai_codes[status.code] += 1
        repairs = await self._active_repairs(abai_ids)
        incidents = await self.incident_status_service.get_for_wells(well_ids)
        stations_by_well: dict[int, list[StationRef]] = defaultdict(list)
        for ref in await self._load_stations(
            await self.station_repository.list_by_well_ids(well_ids),
        ):
            if ref.active:
                stations_by_well[ref.well_id].append(ref)

        result = {}
        for well in wells:
            sdmo, primary = self._sdmo_input(stations_by_well.get(well.id, []))
            result[well.id] = (
                WellInputs(
                    abai=statuses.get(well.abai_id),
                    repair=repairs.get(well.abai_id),
                    incident=incident_input(incidents.get(well.id)),
                    sdmo=sdmo,
                ),
                primary,
            )
        return result

    async def _active_repairs(self, abai_ids: Sequence[int]) -> dict[int, RepairInput]:
        repairs = await self.repair_repository.list_current_by_abai_well_ids(
            abai_ids,
            now=self.now,
        )
        if not repairs:
            return {}
        intervals = await self.status_repository.list_intervals_by_abai_well_ids(
            list(repairs),
            since=min(repair.start_time for repair in repairs.values()),
            until=self.now + timedelta(seconds=1),
        )
        return active_repairs(repairs, intervals)

    def _sdmo_input(
        self,
        stations: Sequence[StationRef],
    ) -> tuple[SdmoInput, int | None]:
        """Привод скважины — по станции с самым свежим пригодным отсчётом;
        известное состояние важнее неизвестного."""
        if not stations:
            return SdmoInput(), None
        last_seen = max(
            (
                snapshot.last_seen_at
                for station in stations
                if (snapshot := self.snapshots.get(station.id)) is not None
                and snapshot.last_seen_at is not None
            ),
            default=None,
        )
        candidates = []
        for station in stations:
            snapshot = self.snapshots.get(station.id)
            if snapshot is None:
                continue
            outage_since = self.outage_since_by_ngdu.get(station.abai_ngdu_id)
            known = snapshot.is_known(self.now, outage_since=outage_since)
            candidates.append(
                (
                    known,
                    snapshot.last_sample_at is not None,
                    snapshot.last_sample_at or self.now,
                    station,
                ),
            )
        if not candidates:
            return SdmoInput(has_active_station=True, last_seen_at=last_seen), None
        known, _, _, station = max(candidates, key=lambda item: item[:3])
        snapshot = self.snapshots[station.id]
        outage_since = self.outage_since_by_ngdu.get(station.abai_ngdu_id)
        drive = DriveInput(
            station_id=station.id,
            abai_ngdu_id=station.abai_ngdu_id,
            running=snapshot.running,
            since=snapshot.since,
            known=known,
            last_sample_at=snapshot.last_sample_at,
        )
        return (
            SdmoInput(
                has_active_station=True,
                drive=drive,
                last_seen_at=last_seen,
                outage_since=outage_since,
            ),
            station.id,
        )
