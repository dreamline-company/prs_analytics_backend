"""Правило R10: состояние скважины на дату фиксации по замерам ЦИТС.

Перенос ``evaluate()`` из скрипта автора ``cits_events.py`` (2026-09-18.5)
с сохранением порядка проверок — он существенен. Каждая скважина на дату
фиксации находится ровно в одном состоянии: норма, одно из событий 1–4 или
строка сервисного перечня.

  1  Qж <= 0.7 x техрежим на 3 замерах подряд, серия свежая, до неё норма
  2  2 нулевых замера подряд + признак работы СУ
  3  последний замер старше STALE_D (у периодической эксплуатации —
     STALE_D_PERIODIC) + признак работы СУ за последние сутки
  4  последний замер пустой

Скважины в простое ABAI не оцениваются (правило длительности): простой,
переходящий через сутки или открытый, вырезает свои дни из ряда; простой
внутри суток не вырезает, а попадает в примечание.

Модуль чистый — без базы и ORM: вход собирает раннер. Признак СУ запрашивается
через ``SuSource``: раннер сначала прогоняет правило с записывающим
источником (``su_requests``), чтобы узнать нужные сутки, а потом — с данными
(``MappingSu``). Ветвление правила от значений СУ не зависит, поэтому набор
запрошенных суток один и тот же.
"""

import statistics
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Protocol

from apps.detectors.cits_events import config

EVENT_DROP = 1
EVENT_ZERO = 2
EVENT_STALE = 3
EVENT_NULL = 4

SU_RUN = "СУ работает"
SU_STOP = "СУ стоит, статуса простоя нет"
SU_PARTIAL = "СУ работала часть дней серии"
SU_NONE = "нет данных СУ"

KLASS_DROP = "отклонение от техрежима, свежее"
KLASS_ZERO = "нулевые замеры"
KLASS_ZERO_ROUTINE = "замеры неустойчивые (нули чередуются с подачей)"
KLASS_ZERO_PERIODIC = "нули, периодическая эксплуатация"
KLASS_STALE = "замер устарел"
KLASS_NULL = "NULL по жидкости"

# Коды сервисного перечня (для людей — ServiceItem.title).
SERVICE_CHRONIC_LOW = "chronic_low"
SERVICE_CHRONIC_HIGH = "chronic_high"
SERVICE_PLAN_MISMATCH = "plan_mismatch"
SERVICE_NO_REGIME = "no_regime"
SERVICE_REGIME_OUTDATED = "regime_outdated"
SERVICE_NULL_LIQUID = "null_liquid"
SERVICE_STATUS_MISMATCH = "status_mismatch"


@dataclass(frozen=True, slots=True)
class Measurement:
    """Суточная медиана замеров Qж, м³/сут; None — все замеры суток пустые."""

    day: date
    q: float | None


@dataclass(frozen=True, slots=True)
class RegimeInterval:
    """Интервал техрежима: начало действия и плановый Qж, м³/сут."""

    start: date
    liquid: float | None


@dataclass(frozen=True, slots=True)
class StatusInterval:
    """Интервал статуса ABAI в местных сутках; ``end`` None — не закрыт."""

    start: date
    end: date | None
    code: str | None
    name: str
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class SuDay:
    """Записи СДМО за сутки: сколько с регистром 1998 и сколько из них > 0."""

    n: int
    n_run: int


class SuSource(Protocol):
    def day(self, day: date) -> SuDay | None: ...


@dataclass(frozen=True, slots=True)
class WellInput:
    name: str
    series: Sequence[Measurement]  # по возрастанию дня
    regimes: Sequence[RegimeInterval]  # по возрастанию начала
    statuses: Sequence[StatusInterval]  # по возрастанию начала


@dataclass(frozen=True, slots=True)
class Event:
    event: int
    klass: str
    last_day: date
    age_d: int
    q_last: float | None = None
    rezhim: float | None = None
    dev: float | None = None
    n_series: int | None = None
    series_from: date | None = None
    prev_dev: float | None = None
    su: str | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class ServiceItem:
    kind: str
    title: str
    detail: str


@dataclass(frozen=True, slots=True)
class WellVerdict:
    """Состояние скважины на дату фиксации.

    ``tail_bad`` нужен эпизоду, а не правилу: последний учтённый замер всё
    ещё нулевой или за порогом отклонения. True — плохой, False — в норме,
    None — не оценить (нет замеров или техрежима).
    """

    event: Event | None = None
    service: tuple[ServiceItem, ...] = ()
    idle: bool = False
    tail_bad: bool | None = None

    @property
    def incident_event(self) -> Event | None:
        """Событие, из которого растёт эпизод (1 или 2)."""
        if self.event is not None and self.event.event in {EVENT_DROP, EVENT_ZERO}:
            return self.event
        return None

    @property
    def stale(self) -> bool:
        return self.event is not None and self.event.event == EVENT_STALE

    @property
    def chronic(self) -> bool:
        return any(item.kind == SERVICE_CHRONIC_LOW for item in self.service)


@dataclass(slots=True)
class _StatusView:
    excluded: set[date] = field(default_factory=set)
    sameday: dict[date, list[str]] = field(default_factory=lambda: defaultdict(list))
    periodic: bool = False
    idle_now: StatusInterval | None = None


def _join(*parts: str) -> str:
    return "; ".join(part for part in parts if part)


def _tail_run(flags: Sequence[bool]) -> int:
    n = 0
    for flag in reversed(flags):
        if not flag:
            break
        n += 1
    return n


def _days(first: date, last: date) -> list[date]:
    return [first + timedelta(days=i) for i in range((last - first).days + 1)]


def recent_days(fix: date) -> list[date]:
    """Последние SU_RECENT_D суток по дату фиксации включительно."""
    return _days(fix - timedelta(days=config.SU_RECENT_D - 1), fix)


def rezhim_on(
    regimes: Sequence[RegimeInterval],
    day: date,
) -> tuple[float | None, str, str]:
    """Техрежим, действовавший на дату замера.

    Возвращает (значение, код сервиса, пояснение); значение None — техрежима
    нет или он старше TM_MAX_AGE_D.
    """
    best: RegimeInterval | None = None
    for regime in regimes:
        if regime.start > day:
            break
        best = regime
    if best is None or best.liquid is None or best.liquid <= 0:
        return None, SERVICE_NO_REGIME, "нет техрежима"
    if (day - best.start).days > config.TM_MAX_AGE_D:
        return None, SERVICE_REGIME_OUTDATED, f"техрежим устарел (от {best.start})"
    return best.liquid, "", ""


def status_view(
    statuses: Sequence[StatusInterval],
    fix: date,
    dfrom: date,
) -> _StatusView:
    view = _StatusView()
    for status in statuses:
        end = status.end or fix
        active_on_fix = status.start <= fix and (
            status.end is None or status.end >= fix
        )
        if status.code in config.WORK_STATUSES:
            if status.code == config.STATUS_PERIODIC and active_on_fix:
                view.periodic = True
            continue
        if status.code == config.STATUS_IDLE and status.end == status.start:
            view.sameday[status.start].append(status.reason or "причина не указана")
            continue
        for day in _days(max(status.start, dfrom), min(end, fix)):
            view.excluded.add(day)
        if active_on_fix:
            view.idle_now = status
    return view


def su_state(su: SuSource | None, day: date) -> tuple[str, float | None]:
    record = su.day(day) if su is not None else None
    if record is None or record.n < config.SU_MIN_RECORDS:
        return SU_NONE, None
    share = record.n_run / record.n
    return (SU_RUN if share > config.SU_RUN_SHARE else SU_STOP), share


def _su_label(states: Sequence[tuple[str, float | None]]) -> str:
    kinds = [state for state, _ in states if state != SU_NONE]
    if not kinds:
        return SU_NONE
    if all(kind == SU_RUN for kind in kinds):
        return SU_RUN
    if kinds[-1] == SU_STOP:
        return SU_STOP
    return SU_PARTIAL


def _su_shares(states: Sequence[tuple[str, float | None]], title: str) -> str:
    shares = [share for _, share in states if share is not None]
    if not shares:
        return ""
    return title + ", ".join(f"{share:.0%}" for share in shares[-7:])


def _sameday_note(reasons: Sequence[str]) -> str:
    return "; ".join(
        f"простой в сутки замера: {reason}"
        + (f" (x{reasons.count(reason)})" if reasons.count(reason) > 1 else "")
        for reason in dict.fromkeys(reasons)
    )


def evaluate(  # noqa: C901, PLR0911, PLR0912, PLR0915
    well: WellInput,
    fix: date,
    su: SuSource | None,
) -> WellVerdict:
    """Состояние скважины на конец суток ``fix``.

    ``su`` None — СДМО не подключена: признак СУ «нет данных», сверка простоя
    с работой СУ не делается.
    """
    dfrom = fix - timedelta(days=config.WINDOW_D)
    view = status_view(well.statuses, fix, dfrom)

    if view.idle_now is not None:
        service = ()
        idle = view.idle_now
        if su is not None and (fix - idle.start).days >= config.SU_RECENT_D:
            states = [su_state(su, day) for day in recent_days(fix)]
            if all(state == SU_RUN for state, _ in states):
                service = (
                    ServiceItem(
                        SERVICE_STATUS_MISMATCH,
                        "статус ABAI не соответствует факту",
                        f"{idle.name} / {idle.reason or ''} с {idle.start}, "
                        "СУ работает",
                    ),
                )
        return WellVerdict(service=service, idle=True)

    series = [m for m in well.series if m.day <= fix]
    if not series:
        return WellVerdict()
    last_day = series[-1].day
    age = (fix - last_day).days
    note = _sameday_note(view.sameday.get(last_day, []))
    if view.periodic:
        note = _join("периодическая эксплуатация", note)

    stale_d = config.STALE_D_PERIODIC if view.periodic else config.STALE_D
    if age > stale_d:
        states = [su_state(su, day) for day in recent_days(fix)]
        su_lbl = _su_label(states)
        note = _join(note, _su_shares(states, "доля работы СУ за последние сутки: "))
        return WellVerdict(
            event=Event(
                event=EVENT_STALE,
                klass=f"{KLASS_STALE}, {su_lbl}",
                last_day=last_day,
                age_d=age,
                su=su_lbl,
                note=note,
            ),
        )

    if series[-1].q is None:
        return WellVerdict(
            event=Event(
                event=EVENT_NULL,
                klass=KLASS_NULL,
                last_day=last_day,
                age_d=age,
                note=note,
            ),
            service=(
                ServiceItem(
                    SERVICE_NULL_LIQUID,
                    "NULL по жидкости в ЦИТС",
                    f"замер {last_day}",
                ),
            ),
        )

    valid = [m for m in series if m.q is not None and m.day not in view.excluded]
    if not valid:
        return WellVerdict()
    q_last = valid[-1].q
    rz_last, rz_kind, rz_detail = rezhim_on(well.regimes, valid[-1].day)

    zero = [m.q <= config.ZERO_EPS for m in valid]
    zr = _tail_run(zero)
    if zr >= config.N_ZERO:
        prev = zero[:-zr][-config.PREV_N :]
        routine = bool(prev) and sum(prev) / len(prev) >= config.ZERO_PREV_SHARE
        zero_days = [m.day for m in valid[-zr:]]
        states = [su_state(su, day) for day in zero_days]
        su_lbl = _su_label(states)
        note = _join(note, _su_shares(states, "доля работы СУ в дни нулей: "))
        if view.periodic:
            klass = KLASS_ZERO_PERIODIC
        elif routine:
            klass = f"{KLASS_ZERO_ROUTINE}, {su_lbl}"
        else:
            klass = f"{KLASS_ZERO}, {su_lbl}"
        return WellVerdict(
            event=Event(
                event=EVENT_ZERO,
                klass=klass,
                last_day=last_day,
                age_d=age,
                q_last=q_last,
                rezhim=rz_last,
                n_series=zr,
                series_from=zero_days[0],
                su=su_lbl,
                note=note,
            ),
            tail_bad=True,
        )

    if rz_last is None:
        return WellVerdict(
            service=(
                ServiceItem(rz_kind, rz_detail, f"замер {last_day}: {q_last:.1f}"),
            ),
            tail_bad=True if zero[-1] else None,
        )

    tail_bad = zero[-1] or q_last / rz_last - 1 <= -config.DEV_THR
    known: list[tuple[date, float, bool]] = []
    for m, is_zero in zip(valid, zero, strict=True):
        rz, _, _ = rezhim_on(well.regimes, m.day)
        if rz is not None:
            known.append((m.day, m.q / rz - 1, is_zero))
    nonzero = [dev for _, dev, is_zero in known if not is_zero]
    med_all = statistics.median(nonzero) if nonzero else None

    low = [dev <= -config.DEV_THR and not is_zero for _, dev, is_zero in known]
    lr = _tail_run(low)
    if lr >= config.N_DEV:
        start = known[-lr][0]
        prev = [dev for _, dev, is_zero in known[:-lr] if not is_zero][-config.PREV_N :]
        prev_med = statistics.median(prev) if len(prev) >= config.PREV_MIN else None
        dur = (fix - start).days
        last_dev = known[-1][1]
        if prev_med is not None and prev_med >= config.UP_THR:
            item = ServiceItem(
                SERVICE_PLAN_MISMATCH,
                "замеры не соответствуют техрежиму (ранее выше плана)",
                f"было {prev_med:+.0%}, сейчас {last_dev:+.0%}",
            )
            return WellVerdict(service=(item,), tail_bad=tail_bad)
        if (
            dur <= config.FRESH_D
            and prev_med is not None
            and prev_med > config.PREV_NORM
        ):
            return WellVerdict(
                event=Event(
                    event=EVENT_DROP,
                    klass=KLASS_DROP,
                    last_day=last_day,
                    age_d=age,
                    q_last=q_last,
                    rezhim=rz_last,
                    dev=last_dev,
                    n_series=lr,
                    series_from=start,
                    prev_dev=prev_med,
                    note=note,
                ),
                tail_bad=tail_bad,
            )
        item = ServiceItem(
            SERVICE_CHRONIC_LOW,
            "хроническое отклонение вниз",
            f"{last_dev:+.0%}, серия {lr} замеров с {start}; "
            f"медиана за период {med_all:+.0%}",
        )
        return WellVerdict(service=(item,), tail_bad=tail_bad)
    if med_all is not None and med_all >= config.UP_THR:
        item = ServiceItem(
            SERVICE_CHRONIC_HIGH,
            "хроническое отклонение вверх",
            f"медиана за период {med_all:+.0%}, техрежим {rz_last:g}",
        )
        return WellVerdict(service=(item,), tail_bad=tail_bad)
    return WellVerdict(tail_bad=tail_bad)


class RecordingSu:
    """Источник СУ первого прохода: ничего не знает, запоминает запросы."""

    def __init__(self) -> None:
        self.days: set[date] = set()

    def day(self, day: date) -> SuDay | None:
        self.days.add(day)
        return None


class MappingSu:
    """Источник СУ второго прохода: сутки из заранее выбранных данных."""

    def __init__(self, days: dict[date, SuDay]) -> None:
        self._days = days

    def day(self, day: date) -> SuDay | None:
        return self._days.get(day)


def su_requests(wells: Sequence[WellInput], fix: date) -> dict[str, set[date]]:
    """Какие сутки СУ правило запросит по каждой скважине."""
    requests: dict[str, set[date]] = {}
    for well in wells:
        recorder = RecordingSu()
        evaluate(well, fix, recorder)
        if recorder.days:
            requests[well.name] = recorder.days
    return requests
