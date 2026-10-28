"""Итоговое состояние скважины по входам — чистая функция, без базы.

Приоритет, если сработало несколько условий (решения 28.10.2026, план —
docs/well_state_plan.md): repair > inactive > stopped > alarm > no_link >
warning > working.

- repair — ремонт идёт (``Repair.is_open`` и после его начала в ABAI не было
  WRK/PEXP — это проверяет сборщик входов) и привод не работает;
- inactive — статус ABAI «вне эксплуатации» и привод не работает;
- stopped — привод не работает по СДМО; без СДМО — простой в ABAI;
- alarm — эпизод детектора уровня alarm (неизвестный уровень — тоже alarm);
- no_link — станция есть, но молчит от часа до 7 суток, статус WRK/PEXP или
  статуса нет; дольше 7 суток станция считается снятой;
- warning — ABAI говорит «не работает», а привод работает; или эпизод
  уровня warning;
- working — всё остальное.

Во время сбоя СДМО по НГДУ состояние привода заморожено (сборщик отдаёт его
известным, если оно было свежим к началу сбоя), а «нет связи» ставится только
станциям, замолчавшим ещё до сбоя.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from apps.detectors.models.incident import INCIDENT_LEVEL_WARNING
from apps.wells.models.well_state import (
    SOURCE_ABAI,
    SOURCE_DETECTOR,
    SOURCE_REPAIR,
    SOURCE_SDMO,
    STATE_ALARM,
    STATE_INACTIVE,
    STATE_NO_LINK,
    STATE_REPAIR,
    STATE_STOPPED,
    STATE_WARNING,
    STATE_WORKING,
)
from apps.wells.services.well_state.drive import NO_LINK_GAP

# После стольких суток молчания станция считается снятой: скважина живёт по
# правилам ABAI, а не висит в «нет связи».
STATION_LOST_AFTER = timedelta(days=7)

ABAI_WORK = frozenset({"WRK"})
ABAI_PERIODIC = frozenset({"PEXP", "CNTM"})
ABAI_IDLE = frozenset({"DWN", "NINF", "ADEN"})
# Остальные коды справочника (консервация, ликвидация, бездействие,
# наблюдательные, резерв, бурение…), неизвестные и пустой код — вне эксплуатации.
# Справочник emg.well_status_type на 28.10.2026 — незнакомый код пишется в лог.
ABAI_KNOWN_CODES = frozenset(
    {
        "DRL", "INFR", "WRK", "DWN", "CNS", "LCV", "PEXP", "SLP", "PRJ", "WLCV",
        "WCNS", "NINF", "BLCN", "CNTM", "OBS", "RSV", "MTG", "TCON", "ADEN",
        "CDRL", "REAN", "WDEV", "RTS",
    },
)  # fmt: skip

KIND_WORK = "work"
KIND_PERIODIC = "periodic"
KIND_IDLE = "idle"
KIND_INACTIVE = "inactive"


@dataclass(frozen=True, slots=True)
class AbaiStatusInput:
    code: str | None
    name: str
    reason: str | None
    since: datetime

    @property
    def kind(self) -> str:
        if self.code in ABAI_WORK:
            return KIND_WORK
        if self.code in ABAI_PERIODIC:
            return KIND_PERIODIC
        if self.code in ABAI_IDLE:
            return KIND_IDLE
        return KIND_INACTIVE

    @property
    def label(self) -> str:
        """Что писать в причине: причина простоя, а без неё — статус."""
        return self.reason or self.name


@dataclass(frozen=True, slots=True)
class RepairInput:
    repair_id: int
    type_name: str | None
    since: datetime


@dataclass(frozen=True, slots=True)
class IncidentInput:
    level: str  # худший уровень активных эпизодов (не normal)
    title: str
    since: datetime  # эскалация для alarm, иначе открытие
    incident_id: int | None = None


@dataclass(frozen=True, slots=True)
class DriveInput:
    station_id: int
    abai_ngdu_id: int  # НГДУ станции — по нему сбой СДМО
    running: bool | None
    since: datetime | None
    known: bool  # свежее подтверждённое состояние (или заморожено сбоем)
    last_sample_at: datetime | None


@dataclass(frozen=True, slots=True)
class SdmoInput:
    has_active_station: bool = False
    drive: DriveInput | None = None  # станция с самым свежим отсчётом
    last_seen_at: datetime | None = None  # последний любой отсчёт активных станций
    outage_since: datetime | None = None  # сбой СДМО по НГДУ станции


@dataclass(frozen=True, slots=True)
class WellInputs:
    abai: AbaiStatusInput | None = None
    repair: RepairInput | None = None
    incident: IncidentInput | None = None
    sdmo: SdmoInput = field(default_factory=SdmoInput)


@dataclass(frozen=True, slots=True)
class Decision:
    state: str
    source: str
    reason_code: str
    reason_ru: str
    started_at: datetime | None  # начало по данным; None — неизвестно
    evidence: dict


def _fmt(moment: datetime) -> str:
    return moment.strftime("%d.%m %H:%M")


def _latest(*moments: datetime | None) -> datetime | None:
    known = [moment for moment in moments if moment is not None]
    return max(known) if known else None


def decide(inputs: WellInputs, now: datetime) -> Decision:  # noqa: C901, PLR0911, PLR0912
    abai, repair, incident, sdmo = (
        inputs.abai,
        inputs.repair,
        inputs.incident,
        inputs.sdmo,
    )
    evidence = _evidence(inputs)
    kind = abai.kind if abai is not None else None
    drive = sdmo.drive if sdmo.drive is not None and sdmo.drive.known else None
    drive_on = drive is not None and drive.running is True
    drive_off = drive is not None and drive.running is False

    silent_since = lost_since = None
    # Во время сбоя СДМО по НГДУ молчат все станции: «нет связи» остаётся
    # только у тех, что замолчали ещё до сбоя.
    silenced_by_outage = (
        sdmo.outage_since is not None
        and sdmo.last_seen_at is not None
        and sdmo.last_seen_at >= sdmo.outage_since - NO_LINK_GAP
    )
    if (
        sdmo.has_active_station
        and drive is None
        and not silenced_by_outage
        and sdmo.last_seen_at is not None
        and now - sdmo.last_seen_at > NO_LINK_GAP
    ):
        if now - sdmo.last_seen_at <= STATION_LOST_AFTER:
            silent_since = sdmo.last_seen_at
        else:
            lost_since = sdmo.last_seen_at

    def make(
        state: str,
        source: str,
        code: str,
        text: str,
        started: datetime | None,
    ) -> Decision:
        return Decision(state, source, code, text, started, evidence)

    if repair is not None and not drive_on:
        name = repair.type_name or "вид работ не указан"
        return make(
            STATE_REPAIR,
            SOURCE_REPAIR,
            "repair",
            f"Ремонт: {name}",
            repair.since,
        )

    if abai is not None and kind == KIND_INACTIVE and not drive_on:
        return make(
            STATE_INACTIVE,
            SOURCE_ABAI,
            "abai_inactive",
            f"{abai.name} (ABAI)",
            abai.since,
        )

    # Начало по приводу, но не раньше смены статуса ABAI: скважина, которую
    # вывели из консервации в WRK со стоящим приводом, остановлена с момента
    # смены статуса, а не с начала истории станции.
    status_since = abai.since if abai is not None and kind != KIND_IDLE else None
    if drive_off and kind != KIND_PERIODIC:
        text = "Привод остановлен (СДМО)"
        if abai is not None and kind == KIND_IDLE:
            text += f", в ABAI «{abai.label}»"
        started = _latest(drive.since, status_since)
        return make(STATE_STOPPED, SOURCE_SDMO, "drive_off", text, started)
    if drive is None and abai is not None and kind == KIND_IDLE:
        return make(
            STATE_STOPPED,
            SOURCE_ABAI,
            "abai_idle",
            f"Простой: {abai.label} (ABAI)",
            abai.since,
        )

    if incident is not None and incident.level != INCIDENT_LEVEL_WARNING:
        # Эпизоды без данных сами не закрываются: при умершей станции авария
        # скрыла бы «нет связи» — пишем это в причине.
        text = incident.title
        if silent_since is not None:
            text += f"; нет данных СДМО с {_fmt(silent_since)}"
        return make(
            STATE_ALARM,
            SOURCE_DETECTOR,
            "detector_alarm",
            text,
            incident.since,
        )

    if silent_since is not None and kind in (KIND_WORK, KIND_PERIODIC, None):
        return make(
            STATE_NO_LINK,
            SOURCE_SDMO,
            "sdmo_silent",
            f"Нет данных СДМО с {_fmt(silent_since)}",
            silent_since,
        )
    if abai is None and drive is None:
        return make(
            STATE_NO_LINK,
            SOURCE_ABAI,
            "no_data",
            "Нет ни статуса ABAI, ни данных СДМО",
            None,
        )

    if drive_on and (repair is not None or kind in (KIND_IDLE, KIND_INACTIVE)):
        if repair is not None:
            what = f"ремонт «{repair.type_name or 'вид работ не указан'}»"
            source, started = SOURCE_REPAIR, _latest(drive.since, repair.since)
        else:
            label = abai.label if kind == KIND_IDLE else abai.name
            what = f"«{label}»"
            source, started = SOURCE_ABAI, _latest(drive.since, abai.since)
        return make(
            STATE_WARNING,
            source,
            "abai_conflict",
            f"В ABAI {what}, привод работает",
            started,
        )
    if incident is not None:
        return make(
            STATE_WARNING,
            SOURCE_DETECTOR,
            "detector_warning",
            incident.title,
            incident.since,
        )

    if abai is not None and kind == KIND_PERIODIC:
        return make(
            STATE_WORKING,
            SOURCE_ABAI,
            "abai_periodic",
            "Периодическая эксплуатация",
            abai.since,
        )
    if drive_on:
        started = _latest(drive.since, status_since)
        return make(STATE_WORKING, SOURCE_SDMO, "drive_on", "Привод работает", started)
    text = f"{abai.name} (ABAI)"
    if lost_since is not None:
        text += f"; СДМО молчит с {_fmt(lost_since)}"
    return make(STATE_WORKING, SOURCE_ABAI, "abai_work", text, abai.since)


def _iso(moment: datetime | None) -> str | None:
    return moment.isoformat() if moment is not None else None


def _evidence(inputs: WellInputs) -> dict:
    """Входы в JSON-виде — для «почему так» и разбора."""
    result: dict = {"sdmo_outage_since": _iso(inputs.sdmo.outage_since)}
    if inputs.abai is not None:
        result["abai"] = {
            "code": inputs.abai.code,
            "status": inputs.abai.name,
            "reason": inputs.abai.reason,
            "since": _iso(inputs.abai.since),
        }
    if inputs.repair is not None:
        result["repair"] = {
            "id": inputs.repair.repair_id,
            "type": inputs.repair.type_name,
            "since": _iso(inputs.repair.since),
        }
    if inputs.incident is not None:
        result["incident"] = {
            "id": inputs.incident.incident_id,
            "level": inputs.incident.level,
            "title": inputs.incident.title,
            "since": _iso(inputs.incident.since),
        }
    drive = inputs.sdmo.drive
    if drive is not None:
        result["drive"] = {
            "station_id": drive.station_id,
            "abai_ngdu_id": drive.abai_ngdu_id,
            "running": drive.running,
            "since": _iso(drive.since),
            "known": drive.known,
            "last_sample_at": _iso(drive.last_sample_at),
        }
    if inputs.sdmo.has_active_station:
        result["sdmo_last_seen_at"] = _iso(inputs.sdmo.last_seen_at)
    return result
