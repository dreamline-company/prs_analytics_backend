"""Хронология событий скважины — чистая функция, без базы.

Случай — один отказ скважины: сигнал детектора → поломка → начало ремонта
(план и решения 28.10.2026 — docs/well_timeline_plan.md).

- Якорь — ремонт не-ГТМ. Поломка — начало самого раннего простоя-отказа в
  непрерывной цепочке нерабочих статусов ABAI перед ремонтом, не глубже 60 сут
  и не раньше конца предыдущего ремонта. Если отдельного простоя нет
  (скважина ушла из работы сразу в «ПРС»), поломки нет — прочерк, дату
  ремонта в неё не подставляем.
- Простой-отказ без ремонта — тоже случай, в том числе хвост цепочки после
  конца ремонта (ремонт не помог, скважина так и стоит). Точки отсчёта нет —
  поломку ищем не глубже 60 сут до последнего простоя-отказа. Статус
  «ПРС»/«КРС» без строки ремонта даёт узел ремонта из ABAI: загрузка ремонтов
  отстаёт от статусов.
- Ремонт, не закрытый в ABAI, кончается на первом статусе после начала,
  кроме простоя, или на начале следующего ремонта (как ``active_repairs`` в
  well_state). Идущий статус ABAI, после которого начался другой, закрыт его
  началом: открытые интервалы перечитываются раз в час.
- Сигнал — факт срабатывания детектора, не прогноз: эпизоды, открытые в окне
  [поломка − 14 сут, начало ремонта], кроме ложных и открытых внутри ремонта
  (строки ремонта или статуса «ПРС»/«КРС»): тревога во время работ — артефакт.
  Эпизод, у которого проверка нашла ремонт случая, привязан к нему и вне окна.
  Эпизод, подходящий к двум случаям, идёт к более раннему. Случаи вне периода
  сигналы не забирают.
- Идущие эпизоды без отказа — один открытый случай «сигнал»; закрытые без
  отказа в хронологию не попадают.

Все времена — местные наивные: ABAI, ремонты и эпизоды пишутся в местном.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from apps.detectors.models.incident import INCIDENT_STATUS_ACTIVE
from apps.detectors.models.verification import VERDICT_FALSE_ALARM
from apps.detectors.rod_breaks.verification.rule import (
    AbaiStatusEvent,
    is_abai_failure,
)
from apps.repairs.services.repair_classifier import is_gtm_repair
from apps.wells.services.well_state.rules import ABAI_IDLE, ABAI_PERIODIC, ABAI_WORK
from apps.wells.services.well_timeline import config

CASE_FAILURE = "failure"
CASE_SIGNAL = "signal"

REPAIR_SOURCE_REPAIR = "repair"
REPAIR_SOURCE_ABAI = "abai"

_WORKING_CODES = ABAI_WORK | ABAI_PERIODIC
# Причина простоя — вид работ: «TP 4-1 Ревизия и смена насоса», «КР-12 …».
# Скважина стоит в ожидании ремонта этого вида. Буквы бывают и латинские, и
# кириллические.
_WORK_TYPE_REASON = re.compile(r"^\s*(?:[TТ][PР]|[KК][PР])[\s-]*\d", re.IGNORECASE)
# Статус самого ремонта — причина ровно «ПРС» / «КРС». «ОПРС» (ожидание) — это
# ещё простой, то есть поломка.
_REPAIR_REASONS = frozenset({"прс", "крс"})


@dataclass(frozen=True, slots=True)
class StatusInput:
    """Интервал статуса ABAI; ``until=None`` — статус идёт."""

    since: datetime
    until: datetime | None
    code: str | None
    name: str
    reason: str | None

    @property
    def is_working(self) -> bool:
        return self.code in _WORKING_CODES

    @property
    def is_repair(self) -> bool:
        return (self.reason or "").strip().lower() in _REPAIR_REASONS

    @property
    def is_failure(self) -> bool:
        """Простой означает отказ: признак R2-проверки или вид работ не-ГТМ."""
        if self.is_working or self.is_repair:
            return False
        event = AbaiStatusEvent(self.since, self.until, self.code, self.reason)
        if is_abai_failure(event):
            return True
        reason = self.reason or ""
        return bool(_WORK_TYPE_REASON.match(reason)) and not is_gtm_repair(
            repair_type_id=None,
            repair_type_name=reason,
            work_list=None,
        )

    @property
    def title(self) -> str:
        return self.reason or self.name


@dataclass(frozen=True, slots=True)
class RepairInput:
    """Ремонт ABAI, не удалённый там."""

    repair_id: int
    start: datetime
    end: datetime | None
    repair_type_id: int | None
    type_name: str | None
    work_list: str | None
    abai_repair_type_id: int | None

    @property
    def is_gtm(self) -> bool:
        return is_gtm_repair(
            repair_type_id=self.repair_type_id,
            repair_type_name=self.type_name,
            work_list=self.work_list,
        )


@dataclass(frozen=True, slots=True)
class IncidentInput:
    """Эпизод детектора с отметкой проверки (у R9 и R10 проверки нет)."""

    incident_id: int
    detector_code: str
    title: str
    reason_code: str
    level: str
    status: str
    opened_at: datetime
    verdict: str | None = None
    # Ремонты, которые проверка эпизода нашла доказательством отказа.
    repair_ids: frozenset[int] = frozenset()


@dataclass(frozen=True, slots=True)
class Breakdown:
    at: datetime
    code: str | None
    title: str


@dataclass(frozen=True, slots=True)
class RepairNode:
    at: datetime
    end_at: datetime | None
    source: str
    repair_id: int | None
    kind: str | None
    title: str | None


@dataclass(slots=True)
class TimelineCase:
    kind: str
    is_open: bool
    breakdown: Breakdown | None = None
    repair: RepairNode | None = None
    # Привязанные эпизоды по времени открытия; первый — узел «Сигнал».
    signals: list[IncidentInput] = field(default_factory=list)

    @property
    def anchor(self) -> datetime:
        """Время случая: порядок и отбор по периоду."""
        if self.repair is not None:
            return self.repair.at
        if self.breakdown is not None:
            return self.breakdown.at
        return self.signals[0].opened_at


@dataclass(slots=True)
class _Chain:
    """Непрерывная цепочка нерабочих статусов; ``end=None`` — идёт."""

    start: datetime
    end: datetime | None
    items: list[StatusInput]

    def reaches(self, moment: datetime) -> bool:
        return self.end is None or moment <= self.end + config.CHAIN_GAP


@dataclass(slots=True)
class _Pending:
    """Случай с окном привязки сигналов."""

    case: TimelineCase
    window_from: datetime
    window_to: datetime


def close_stale_statuses(statuses: Sequence[StatusInput]) -> list[StatusInput]:
    """Идущий статус, после которого начался другой, закрыт началом следующего:
    старый интервал ещё может быть открыт до часовой перечитки ABAI."""
    ordered = sorted(statuses, key=lambda s: s.since)
    result = []
    for index, status in enumerate(ordered):
        if status.until is None:
            later = next(
                (s.since for s in ordered[index + 1 :] if s.since > status.since),
                None,
            )
            if later is not None:
                status = replace(status, until=later)  # noqa: PLW2901
        result.append(status)
    return result


def close_hanging_repairs(
    repairs: Sequence[RepairInput],
    statuses: Sequence[StatusInput],
) -> list[RepairInput]:
    """Ремонт, не закрытый в ABAI, кончается на первом статусе после начала,
    кроме простоя, или на начале следующего ремонта — иначе забытый в ABAI
    ремонт шёл бы всегда и глушил сигналы и поломки после себя."""
    ordered = sorted(repairs, key=lambda r: (r.start, r.repair_id))
    result = []
    for index, repair in enumerate(ordered):
        if repair.end is None:
            ends = [
                s.since
                for s in statuses
                if s.since > repair.start and s.code not in ABAI_IDLE
            ]
            ends += [r.start for r in ordered[index + 1 :] if r.start > repair.start]
            if ends:
                repair = replace(repair, end=min(ends))  # noqa: PLW2901
        result.append(repair)
    return result


def build_chains(statuses: Sequence[StatusInput]) -> list[_Chain]:
    chains: list[_Chain] = []
    current: _Chain | None = None
    for status in sorted(statuses, key=lambda s: s.since):
        if status.is_working:
            current = None
            continue
        if current is not None and current.reaches(status.since):
            current.items.append(status)
            if current.end is not None:
                current.end = (
                    None if status.until is None else max(current.end, status.until)
                )
            continue
        current = _Chain(status.since, status.until, [status])
        chains.append(current)
    return chains


def _chain_at(chains: Sequence[_Chain], moment: datetime) -> _Chain | None:
    """Цепочка, внутри которой начался ремонт (статус ремонта мог начаться
    чуть позже строки ремонта — ABAI округляет до минуты)."""
    found = None
    for chain in chains:
        if chain.start <= moment + config.SAME_MOMENT and chain.reaches(moment):
            found = chain
    return found


def _tail(chain: _Chain, after: datetime) -> _Chain | None:
    """Часть цепочки, начавшаяся после конца ремонта."""
    items = [s for s in chain.items if s.since >= after - config.SAME_MOMENT]
    if not items:
        return None
    return _Chain(items[0].since, chain.end, items)


def _earliest(statuses: Sequence[StatusInput]) -> Breakdown | None:
    if not statuses:
        return None
    first = min(statuses, key=lambda s: s.since)
    return Breakdown(at=first.since, code=first.code, title=first.title)


def _failure_before(
    items: Sequence[StatusInput],
    *,
    moment: datetime,
    floor: datetime,
) -> Breakdown | None:
    """Самый ранний простой-отказ, начавшийся не раньше ``floor`` и больше
    чем за ``SAME_MOMENT`` до ``moment`` (позже — это уже сам ремонт)."""
    return _earliest(
        [
            s
            for s in items
            if s.is_failure and floor <= s.since < moment - config.SAME_MOMENT
        ],
    )


def _repair_case(
    repair: RepairInput,
    previous: Sequence[RepairInput],
    chains: Sequence[_Chain],
) -> _Pending:
    breakdown = None
    chain = _chain_at(chains, repair.start)
    if chain is not None:
        floor = repair.start - config.CHAIN_MAX_DEPTH
        for other in previous:
            floor = max(floor, other.end or repair.start)
        breakdown = _failure_before(chain.items, moment=repair.start, floor=floor)
    case = TimelineCase(
        kind=CASE_FAILURE,
        is_open=repair.end is None,
        breakdown=breakdown,
        repair=RepairNode(
            at=repair.start,
            end_at=repair.end,
            source=REPAIR_SOURCE_REPAIR,
            repair_id=repair.repair_id,
            kind=config.REPAIR_KIND_BY_ABAI_TYPE.get(repair.abai_repair_type_id),
            title=repair.type_name,
        ),
    )
    start = breakdown.at if breakdown is not None else repair.start
    return _Pending(case, start - config.SIGNAL_LOOKBACK, repair.start)


def _chain_case(chain: _Chain, now: datetime) -> _Pending | None:
    """Простой без строки ремонта: статус «ПРС»/«КРС» или простой-отказ."""
    repair_status = next((s for s in chain.items if s.is_repair), None)
    if repair_status is not None:
        start = repair_status.since
        breakdown = _failure_before(
            chain.items,
            moment=start,
            floor=start - config.CHAIN_MAX_DEPTH,
        )
        case = TimelineCase(
            kind=CASE_FAILURE,
            is_open=chain.end is None,
            breakdown=breakdown,
            repair=RepairNode(
                at=start,
                end_at=repair_status.until,
                source=REPAIR_SOURCE_ABAI,
                repair_id=None,
                kind=(repair_status.reason or "").strip().upper(),
                title=None,
            ),
        )
        first = breakdown.at if breakdown is not None else start
        return _Pending(case, first - config.SIGNAL_LOOKBACK, start)

    failures = [s for s in chain.items if s.is_failure]
    if not failures:
        return None
    # Ремонта нет — отсчёт от последнего простоя-отказа, а не от начала
    # цепочки: она может тянуться годами и обрезана глубиной выборки.
    last = max(failures, key=lambda s: s.since)
    breakdown = _earliest(
        [s for s in failures if s.since >= last.since - config.CHAIN_MAX_DEPTH],
    )
    case = TimelineCase(
        kind=CASE_FAILURE,
        is_open=chain.end is None,
        breakdown=breakdown,
    )
    first = breakdown.at if breakdown is not None else last.since
    return _Pending(case, first - config.SIGNAL_LOOKBACK, chain.end or now)


def _attach(
    pending: Sequence[_Pending],
    incidents: Sequence[IncidentInput],
    busy: Sequence[tuple[datetime, datetime | None]],
) -> list[IncidentInput]:
    """Раздаёт эпизоды случаям; возвращает не привязанные.

    ``busy`` — интервалы ремонтов (строки и статусы «ПРС»/«КРС»).
    """
    ordered = sorted(pending, key=lambda p: p.case.anchor)
    by_repair = {
        p.case.repair.repair_id: p
        for p in pending
        if p.case.repair is not None and p.case.repair.repair_id is not None
    }
    rest = []
    for incident in sorted(incidents, key=lambda i: (i.opened_at, i.incident_id)):
        if incident.verdict == VERDICT_FALSE_ALARM:
            continue
        opened = incident.opened_at
        # Тревога на ремонтируемой скважине — артефакт работ, а не сигнал.
        if any(start < opened and (end is None or opened < end) for start, end in busy):
            continue
        target = next(
            (by_repair[rid] for rid in sorted(incident.repair_ids) if rid in by_repair),
            None,
        ) or next(
            (p for p in ordered if p.window_from <= opened <= p.window_to),
            None,
        )
        if target is None:
            rest.append(incident)
        else:
            target.case.signals.append(incident)
    return rest


def build_cases(
    *,
    statuses: Sequence[StatusInput],
    repairs: Sequence[RepairInput],
    incidents: Sequence[IncidentInput],
    since: datetime,
    now: datetime,
) -> list[TimelineCase]:
    """Случаи скважины с якорем не раньше ``since``: сначала идущие, затем
    от новых к старым.

    ``statuses`` и ``repairs`` нужны с запасом ``HISTORY_DEPTH`` до ``since``:
    поломка ищется до 60 сут назад от ремонта, окно сигнала — ещё 14 сут.
    """
    statuses = close_stale_statuses(statuses)
    repairs = close_hanging_repairs(repairs, statuses)
    chains = build_chains(statuses)

    pending: list[_Pending] = []
    # Цепочка простоя → конец последнего ремонта в ней; None — ремонт идёт.
    repaired: dict[int, datetime | None] = {}
    for index, repair in enumerate(repairs):
        chain = _chain_at(chains, repair.start)
        if chain is not None:
            ended = repaired.get(id(chain), repair.end)
            repaired[id(chain)] = (
                None if ended is None or repair.end is None else max(ended, repair.end)
            )
        # ГТМ — не отказ: случая нет, но цепочку простоя он закрывает.
        if repair.is_gtm:
            continue
        previous = [r for r in repairs[:index] if r.start < repair.start]
        pending.append(_repair_case(repair, previous, chains))

    for chain in chains:
        if id(chain) in repaired:
            ended = repaired[id(chain)]
            # После ремонта скважина так и не заработала — это новый простой.
            tail = _tail(chain, ended) if ended is not None else None
            if tail is None:
                continue
            chain = tail  # noqa: PLW2901
        chain_case = _chain_case(chain, now)
        if chain_case is not None:
            pending.append(chain_case)

    # Отбор по периоду до раздачи сигналов: случай вне периода не должен
    # забирать идущий эпизод — тот остаётся на виду отдельным случаем.
    visible = [p for p in pending if p.case.anchor >= since]
    busy = [(r.start, r.end) for r in repairs] + [
        (s.since, s.until) for s in statuses if s.is_repair
    ]
    rest = _attach(visible, incidents, busy)
    cases = [p.case for p in visible]
    active = [
        i for i in rest if i.status == INCIDENT_STATUS_ACTIVE and i.opened_at >= since
    ]
    if active:
        cases.append(TimelineCase(kind=CASE_SIGNAL, is_open=True, signals=active))

    cases.sort(key=lambda c: (c.is_open, c.anchor), reverse=True)
    return cases
