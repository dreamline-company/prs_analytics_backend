"""Отбор скважин и строки отчёта «Обрыв штанг» — чистая логика, без I/O.

Вход — факты по скважине, собранные на конец дня отчёта (эпизод R2, техрежим,
замеры ГЗУ, статус ABAI, станция). Выход — колонки A–O образца, тексты в его
манере. Всё восстанавливается на дату, поэтому отчёт за прошлый день тот же,
каким был бы утром следующего.
"""

import re
import statistics
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from apps.detectors.dto.internal.r2_loss_report import R2LossRowDTO
from apps.detectors.models.verification import VERDICT_FALSE_ALARM
from apps.detectors.rod_breaks.verification.config import (
    ABAI_FAILURE_REASON_PATTERNS,
    ABAI_WORK_STATUS_CODES,
)
from apps.detectors.services.daily_sheet.selection import IncidentLike
from apps.detectors.services.r2_loss_report import config
from apps.telemetry.services.well_rates import water_cut


@dataclass(frozen=True, slots=True)
class Measure:
    """Замер ЦИТС (ГЗУ): жидкость м³/сут, нефть т/сут."""

    at: datetime
    liquid: float | None
    oil: float | None


@dataclass(frozen=True, slots=True)
class AbaiStatus:
    """Статус ABAI, действующий на конец дня отчёта (время местное)."""

    code: str | None
    name: str
    reason: str | None
    since: datetime

    @property
    def is_work(self) -> bool:
        return self.code in ABAI_WORK_STATUS_CODES


@dataclass(frozen=True, slots=True)
class RepairSpan:
    start: datetime
    end: datetime | None


@dataclass(frozen=True, slots=True)
class DriveFacts:
    """Станция СДМО: норма момента и что было с приводом после сигнала."""

    # Момент за сутки до сигнала начала цепочки — для текста «упала с X до Y».
    base_moment: float | None = None
    # Норма цепочки (за неделю) — для решения, добывает ли скважина.
    reference_moment: float | None = None
    # Последние часы дня: есть ли отсчёты, сколько из них с вращением и
    # медиана момента при вращении.
    has_end_data: bool = False
    end_running: int = 0
    moment_end: float | None = None
    moment_after: float | None = None
    rpm_after: float | None = None
    stopped_at: datetime | None = None
    running_at_end: bool | None = None
    last_running_at: datetime | None = None

    @property
    def moment_share(self) -> float | None:
        if not self.base_moment or self.moment_after is None:
            return None
        return self.moment_after / self.base_moment


@dataclass(slots=True)
class WellFacts:
    incident_id: int
    well_id: int
    well_name: str
    signal_at: datetime
    cdng: str | None = None
    plan_oil: float | None = None
    plan_liquid: float | None = None
    plan_water_cut: float | None = None
    # Скважина вообще есть в данных ЦИТС (не только в окне отчёта).
    in_gzu_data: bool = False
    measures_before: list[Measure] = field(default_factory=list)
    last_before: Measure | None = None
    measures_after: list[Measure] = field(default_factory=list)
    status: AbaiStatus | None = None
    drive: DriveFacts = field(default_factory=DriveFacts)


# Соседние интервалы статусов ABAI с таким зазором — один простой.
_STATUS_JOIN_GAP = timedelta(minutes=1)

type StatusInterval = tuple[datetime, datetime, str | None, str, str | None]


def status_on(
    intervals: Sequence[StatusInterval],
    *,
    moment: datetime,
) -> AbaiStatus | None:
    """Статус ABAI в момент (dbeg, dend, код, название, причина).

    При наложении берётся начавшийся позже. Начало простоя — с первого из
    подряд идущих нерабочих интервалов: смена причины («Нет подачи» → «ОПРС»)
    не обнуляет простой.
    """
    ordered = sorted(intervals, key=lambda item: item[0])
    current = None
    for index, (dbeg, dend, *_rest) in enumerate(ordered):
        if dbeg <= moment < dend:
            current = index
    if current is None:
        return None
    dbeg, _, code, name, reason = ordered[current]
    status = AbaiStatus(code=code, name=name, reason=reason, since=dbeg)
    if status.is_work:
        return status
    since = dbeg
    for prev_beg, prev_end, prev_code, *_rest in reversed(ordered[:current]):
        if prev_code in ABAI_WORK_STATUS_CODES or prev_end < since - _STATUS_JOIN_GAP:
            break
        since = min(since, prev_beg)
    return AbaiStatus(code=code, name=name, reason=reason, since=since)


# --- отбор -------------------------------------------------------------------


def repair_covers(
    opened_at: datetime,
    spans: Sequence[RepairSpan],
    *,
    day_end: datetime,
) -> bool:
    """Эпизод — по ремонту: ремонт заведён около сигнала или позже, либо сигнал
    пришёл в открытом ремонте (бригада запускает привод)."""
    gap = timedelta(hours=config.REPAIR_LOOKBACK_HOURS)
    return any(
        opened_at - gap <= span.start < day_end
        or (span.start < opened_at and span.end is not None and span.end > opened_at)
        for span in spans
    )


def eligible_episodes(
    incidents: Sequence[IncidentLike],
    *,
    verdicts: dict[int, str],
    repairs: dict[int, Sequence[RepairSpan]],
    day_end: datetime,
) -> dict[int, list[IncidentLike]]:
    """Эпизоды по скважинам (по времени), которые могут быть отказом без ремонта.

    Подходит эпизод R2, открытый за ``LOOKBACK_DAYS`` до конца дня, если
    проверка не признала его ложной тревогой и он не по ремонту. Закрыт ли
    эпизод — не важно: добывает ли скважина, решает :func:`is_producing`.
    """
    since = day_end - timedelta(days=config.LOOKBACK_DAYS)
    by_well: dict[int, list[IncidentLike]] = {}
    for incident in incidents:
        if not since <= incident.opened_at < day_end:
            continue
        if verdicts.get(incident.id) == VERDICT_FALSE_ALARM:
            continue
        if repair_covers(
            incident.opened_at,
            repairs.get(incident.well_id, ()),
            day_end=day_end,
        ):
            continue
        by_well.setdefault(incident.well_id, []).append(incident)
    for items in by_well.values():
        items.sort(key=lambda item: item.opened_at)
    return by_well


async def chain_start(
    episodes: Sequence[IncidentLike],
    *,
    worked_between: Callable[[datetime, datetime], Awaitable[bool]],
) -> IncidentLike:
    """Начало последней цепочки: идём от последнего эпизода назад, пока между
    соседними не было нормальной работы."""
    start = len(episodes) - 1
    while start > 0:
        if await worked_between(
            episodes[start - 1].opened_at,
            episodes[start].opened_at,
        ):
            break
        start -= 1
    return episodes[start]


def reference_base(bases: Sequence[float | None]) -> float | None:
    """Норма для цепочки — наибольшая из норм эпизодов скважины."""
    known = [base for base in bases if base]
    return max(known) if known else None


def is_producing(facts: "WellFacts") -> bool:
    """В конце дня скважина добывает: вал крутится и момент у нормы.

    Нет отсчётов станции за последние часы — решает статус ABAI: «в работе»
    считаем работающей (уверенности в отказе нет), простой — нет.
    """
    drive = facts.drive
    if not drive.has_end_data:
        return facts.status is not None and facts.status.is_work
    if drive.end_running < config.END_MIN_RUNNING or drive.moment_end is None:
        return False
    if not drive.reference_moment:
        return True
    return drive.moment_end >= config.PRODUCING_MOMENT_SHARE * drive.reference_moment


# --- колонки -----------------------------------------------------------------

_CDNG_PREFIX = re.compile(r"^ЦДНГ[\s\-–—]*", re.IGNORECASE)


def display_cdng(name: str | None) -> str | None:
    """«ЦДНГ-В.Молдабек» → «В.Молдабек», как в образце; номерные («ЦДНГ-3»)
    остаются полными — одна цифра в колонке ничего не скажет."""
    if not name:
        return None
    short = _CDNG_PREFIX.sub("", name).strip()
    return name.strip() if not short or short.isdigit() else short


def category(plan_oil: float | None) -> str | None:
    if plan_oil is None:
        return None
    if plan_oil < config.CATEGORY_LOW_MAX_OIL:
        return config.CATEGORY_LOW
    if plan_oil < config.CATEGORY_MID_MAX_OIL:
        return config.CATEGORY_MID
    return config.CATEGORY_HIGH


def num(value: float, digits: int = 1) -> str:
    """Число по-русски: «32,5», «0», «2,31»; лишние нули справа убираются."""
    rounded = round(value, digits)
    if rounded == 0:
        return "0"
    text = f"{rounded:.{digits}f}"
    if digits:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def _day(at: datetime) -> str:
    return f"{at:%d.%m}"


def _moment(at: datetime) -> str:
    return f"{at:%d.%m %H:%M}"


# 11–14 склоняются как «замеров», хотя кончаются на 1–4.
_TEENS = range(11, 15)
_MEASURE_FORMS = {1: "замер", 2: "замера", 3: "замера", 4: "замера"}


def _plural_measures(count: int) -> str:
    if count % 100 in _TEENS:
        return "замеров"
    return _MEASURE_FORMS.get(count % 10, "замеров")


def liquid_after_text(facts: WellFacts) -> str:
    """Колонка G: жидкость по замерам ГЗУ после сигнала."""
    if not facts.in_gzu_data:
        return "скважины нет в данных ГЗУ"
    after = [m for m in facts.measures_after if m.liquid is not None]
    if after:
        return _measures_text(after)
    last = facts.last_before
    if last is None or last.liquid is None:
        return "замера после сигнала нет"
    return (
        "замера после сигнала нет; последний до сигнала "
        f"{num(last.liquid)} ({_moment(last.at)})"
    )


def _measures_text(after: Sequence[Measure]) -> str:
    """«0 (06.10, 2 замера)», «0 (08.09, 10.09, 20.10)», «2,3 (05.09); 0 (07.09)»."""
    if len({round(m.liquid or 0, 1) for m in after}) == 1:
        days = [
            _day(datetime.combine(d, datetime.min.time()))
            for d in sorted({m.at.date() for m in after})
        ]
        value = num(after[-1].liquid or 0)
        count = f"{len(after)} {_plural_measures(len(after))}"
        if len(days) == 1:
            return (
                f"{value} ({days[0]}, {count})"
                if len(after) > 1
                else f"{value} ({days[0]})"
            )
        if len(days) <= config.GZU_AFTER_SHOWN:
            return f"{value} ({', '.join(days)})"
        return f"{value} ({count} с {days[0]})"
    shown = after[-config.GZU_AFTER_SHOWN :]
    text = "; ".join(f"{num(m.liquid or 0)} ({_day(m.at)})" for m in shown)
    hidden = len(after) - len(shown)
    return f"{text}; ранее ещё {hidden} {_plural_measures(hidden)}" if hidden else text


def gzu_before(facts: WellFacts) -> tuple[float | str | None, float | str | None]:
    """Колонки J и K: обводнённость и нефть по ГЗУ за 30 суток до сигнала."""
    if not facts.in_gzu_data:
        text = "скважины нет в данных ГЗУ"
        return text, text
    rows = [m for m in facts.measures_before if m.liquid is not None]
    if not rows:
        return "нет замеров", "нет замеров"
    with_liquid = [m for m in rows if m.liquid and m.liquid > 0]
    if with_liquid and all(not m.oil for m in with_liquid):
        avg_liquid = statistics.fmean(m.liquid for m in with_liquid)
        scope = (
            "единственному замеру"
            if len(with_liquid) == 1
            else f"всем {len(with_liquid)} замерам"
        )
        return (
            f"0 по {scope} при жидкости {num(avg_liquid)} — канал "
            "нефти/обводнённости ГЗУ не работает",
            "0 (канал не работает)",
        )
    cuts = [
        cut
        for m in rows
        if (cut := water_cut(liquid_rate=m.liquid, oil_rate=m.oil)) is not None
    ]
    oils = [m.oil for m in rows if m.oil is not None]
    return (
        round(statistics.fmean(cuts)) if cuts else "нет замеров",
        round(statistics.fmean(oils), 2) if oils else "нет замеров",
    )


def _failure_reason(reason: str | None) -> bool:
    text = (reason or "").lower()
    return any(pattern in text for pattern in ABAI_FAILURE_REASON_PATTERNS)


def status_text(facts: WellFacts) -> str:
    """Колонка N: статус ABAI на дату и расхождение с приводом."""
    status = facts.status
    if status is None:
        return "нет статуса"
    if status.is_work:
        return f"{status.name} (статус не соответствует)"
    text = f"{status.name} / {status.reason}" if status.reason else status.name
    if facts.drive.running_at_end:
        text += " (привод работает)"
    return text


def idle_start(facts: WellFacts) -> datetime:
    """С какого момента скважина не добывает: начало простоя ABAI; если в ABAI
    «в работе» — последняя остановка привода, а если он и сейчас крутится
    вхолостую — сам сигнал."""
    status = facts.status
    if status is not None and not status.is_work:
        return status.since
    drive = facts.drive
    if drive.running_at_end:
        return facts.signal_at
    if drive.last_running_at is not None and drive.last_running_at > facts.signal_at:
        return drive.last_running_at
    return drive.stopped_at or facts.signal_at


def idle_days(facts: WellFacts, report_date: date) -> int:
    return max(0, (report_date - idle_start(facts).date()).days)


def what_happens(facts: WellFacts, *, report_date: date) -> str:
    """Колонка L: станция → ABAI → ГЗУ → нетипичная картина."""
    return " ".join(
        part
        for part in (
            _station_text(facts, report_date=report_date),
            _abai_text(facts, report_date=report_date),
            _gzu_text(facts),
            _untypical_text(facts),
        )
        if part
    )


def _station_text(facts: WellFacts, *, report_date: date) -> str:
    drive = facts.drive
    text = f"Станция: {_moment(facts.signal_at)} нагрузка упала"
    share = drive.moment_share
    if share is not None and drive.base_moment is not None:
        text += (
            f" с {num(drive.base_moment, 0)} до {num(drive.moment_after or 0, 0)}"
            f" ({round(share * 100)} %)"
        )
    if drive.rpm_after:
        text += f" при неизменных {num(drive.rpm_after, 0)} об/мин"
    if drive.stopped_at is not None:
        hours = (drive.stopped_at - facts.signal_at).total_seconds() / 3600
        if hours >= config.SPIN_MIN_HOURS:
            text += f", привод вращался без нагрузки {round(hours)} ч, затем остановлен"
        else:
            text += ", затем станция остановлена"
    elif drive.running_at_end:
        days = max(0, (report_date - facts.signal_at.date()).days)
        text += f" и держится {days} сут — привод вращается без нагрузки"
    return text + "."


def _abai_text(facts: WellFacts, *, report_date: date) -> str:
    status = facts.status
    days = idle_days(facts, report_date)
    if status is None:
        return f"Статуса в ABAI нет, ремонта нет {days} сут."
    if status.is_work:
        return f"По ABAI «{status.name}», ремонта нет."
    label = status.reason or status.name
    text = f"В ABAI «{label}» с {_moment(status.since)}"
    if status.reason and not _failure_reason(status.reason):
        text += " — причина не соответствует картине"
    return f"{text}, ремонта нет {days} сут."


def _gzu_text(facts: WellFacts) -> str:
    if not facts.in_gzu_data:
        return "Скважины нет в данных ГЗУ."
    if any(m.liquid is not None for m in facts.measures_after):
        return f"ГЗУ после сигнала: {liquid_after_text(facts)}."
    last = facts.last_before
    if last is None or last.liquid is None:
        return "Замеров ГЗУ после сигнала нет."
    return (
        "Замера ГЗУ после сигнала нет "
        f"(последний до сигнала {num(last.liquid)} м³/сут, {_day(last.at)})."
    )


def _untypical_text(facts: WellFacts) -> str:
    share = facts.drive.moment_share
    if share is None or share < config.UNTYPICAL_MOMENT_SHARE:
        return ""
    return f"Картина не типична для обрыва ({round(share * 100)} %, а не 10–25)."


def oil_loss(facts: WellFacts, share: float) -> float | None:
    if facts.plan_oil is None:
        return None
    return round(facts.plan_oil * share, 1)


def build_row(facts: WellFacts, *, report_date: date) -> R2LossRowDTO:
    # В отчёт попадают только скважины, которые не добывают: недобор — весь
    # режим, как в образце.
    share = 1.0
    water_before, oil_before = gzu_before(facts)
    return R2LossRowDTO(
        incident_id=facts.incident_id,
        well_id=facts.well_id,
        cdng=facts.cdng,
        well_name=facts.well_name,
        category=category(facts.plan_oil),
        plan_water_cut=facts.plan_water_cut,
        plan_oil=facts.plan_oil,
        plan_liquid=facts.plan_liquid,
        liquid_after=liquid_after_text(facts),
        liquid_loss_share=share,
        oil_loss=oil_loss(facts, share),
        water_cut_before=water_before,
        oil_before=oil_before,
        what_happens=what_happens(facts, report_date=report_date),
        signal_date=facts.signal_at.date(),
        abai_status=status_text(facts),
        idle_days=idle_days(facts, report_date),
    )


def sort_rows(rows: Sequence[R2LossRowDTO]) -> list[R2LossRowDTO]:
    """По недобору нефти, крупные сверху; без режима — в конце."""
    return sorted(
        rows,
        key=lambda row: (
            row.oil_loss is None,
            -(row.oil_loss or 0),
            row.well_name,
        ),
    )


def total_oil_loss(rows: Sequence[R2LossRowDTO]) -> float:
    return round(sum(row.oil_loss or 0 for row in rows), 1)
