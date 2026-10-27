"""Межремонтный период (МРП): по скважине и по методике КМГ.

Чистые функции без базы — как ``summarize`` в сводке НГДУ; правила проверяются
тестами без БД.

Два разных расчёта, их нельзя смешивать:

1. **По скважине** (``compute_well_mrp``) — интервалы между соседними ремонтами
   одной скважины: сколько она проработала после каждого ремонта и стало ли это
   меньше, чем было перед ним (индикатор качества ремонта).
2. **По методике КМГ** (``compute_kmg``) — показатель фонда за скользящий год
   (форма ИСУ KMG-F-2429.1-10)::

       МРП с ГТМ   = время работы действующего фонда ÷ число ремонтов
       МРП без ГТМ = время работы действующего фонда ÷ число отказов
       отказы      = ремонты − ГТМ   (минус «по технической причине» — в данных
                                     её нет)

   Время работы — реальное: интервалы статуса «в работе» (WRK) за вычетом
   времени ремонтов. ЧРФ — скважины с N и более ремонтами без ГТМ за период.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, Literal

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

DAY: Final = timedelta(days=1)
# Записи ремонтов с разрывом меньше суток — один ремонт, разбитый на записи.
MERGE_GAP: Final = DAY

Verdict = Literal["worse", "better", "watching", "unknown"]


@dataclass(frozen=True, slots=True)
class RepairSpan:
    id: int
    start: datetime
    end: datetime | None  # None — ремонт ещё идёт
    is_gtm: bool = False
    # «По технической причине»: замена устьевого оборудования или штока — ремонт,
    # но не отказ (в знаменатель МРП без ГТМ не входит)
    is_tech: bool = False


# ---------------------------------------------------------------------------
# МРП по скважине
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RepairMrp:
    repair_id: int
    # МРП до ремонта: конец предыдущего ремонта → начало этого, сут
    before_days: float | None
    # МРП после ремонта: конец этого → начало следующего, сут
    after_days: float | None
    # Следующего ремонта ещё не было — сколько скважина уже работает, сут
    running_days: float | None
    verdict: Verdict
    # Запись склеена с соседней (разрыв меньше суток)
    merged: bool


@dataclass(frozen=True, slots=True)
class WellMrp:
    repairs: list[RepairMrp]
    avg_days: float | None
    intervals_count: int
    repairs_count: int  # после склейки записей
    worse_count: int
    last_end: datetime | None
    gaps_days: list[float] = field(default_factory=list)


@dataclass(slots=True)
class _Group:
    members: list[int]
    start: datetime
    end: datetime | None


def _days(delta: timedelta) -> float:
    return delta / DAY


def _merge(spans: Sequence[RepairSpan]) -> list[_Group]:
    groups: list[_Group] = []
    for span in sorted(spans, key=lambda s: (s.start, s.id)):
        last = groups[-1] if groups else None
        if (
            last is not None
            and last.end is not None
            and span.start - last.end < MERGE_GAP
        ):
            last.members.append(span.id)
            last.end = None if span.end is None else max(last.end, span.end)
        else:
            groups.append(_Group([span.id], span.start, span.end))
    return groups


def compute_well_mrp(spans: Sequence[RepairSpan], *, now: datetime) -> WellMrp:
    """МРП до/после каждого ремонта скважины и индикатор качества.

    ``worse`` — после ремонта скважина проработала меньше, чем перед ним.
    Если следующего ремонта нет, ремонт ``better`` при условии, что скважина
    уже отработала не меньше прежнего МРП, иначе ``watching`` (рано судить).
    Любое сокращение считается ухудшением — порога значимости в методике нет.
    """
    groups = _merge(spans)

    def gap(i: int) -> float | None:
        prev = groups[i - 1]
        if prev.end is None:
            return None
        value = _days(groups[i].start - prev.end)
        return value if value >= 0 else None

    gaps: list[float] = []
    items: list[RepairMrp] = []
    worse = 0

    for i, group in enumerate(groups):
        before = gap(i) if i > 0 else None
        after = gap(i + 1) if i < len(groups) - 1 else None
        if before is not None:
            gaps.append(before)

        running: float | None = None
        if i == len(groups) - 1 and group.end is not None:
            running = max(0.0, _days(now - group.end))

        verdict: Verdict = "unknown"
        if before is not None and after is not None:
            verdict = "worse" if after < before else "better"
        elif before is not None and running is not None:
            verdict = "better" if running >= before else "watching"
        if verdict == "worse":
            worse += 1

        items.extend(
            RepairMrp(
                repair_id=repair_id,
                before_days=before,
                after_days=after,
                running_days=running,
                verdict=verdict,
                merged=len(group.members) > 1,
            )
            for repair_id in group.members
        )

    return WellMrp(
        repairs=items,
        avg_days=sum(gaps) / len(gaps) if gaps else None,
        intervals_count=len(gaps),
        repairs_count=len(groups),
        worse_count=worse,
        last_end=groups[-1].end if groups else None,
        gaps_days=gaps,
    )


def _plural(n: int, one: str, few: str, many: str) -> str:
    """Русское склонение числительного: 1 интервал, 2 интервала, 5 интервалов."""
    if 11 <= n % 100 <= 14:  # noqa: PLR2004
        return many
    return {1: one, 2: few, 3: few, 4: few}.get(n % 10, many)


def interpret_well_mrp(
    well_name: str,
    mrp: WellMrp,
    *,
    repair_id: int | None = None,
) -> str:
    """Текстовая интерпретация для ИИ-ассистента: считает код, не модель."""
    if mrp.repairs_count < 2 or mrp.avg_days is None:  # noqa: PLR2004
        return (
            f"По скважине {well_name} недостаточно истории ремонтов, чтобы "
            "рассчитать межремонтный период (нужно не менее двух ремонтов)."
        )

    parts = [
        (
            f"Средний МРП скважины {well_name}: {mrp.avg_days:.0f} сут "
            f"({mrp.intervals_count} "
            f"{_plural(mrp.intervals_count, 'интервал', 'интервала', 'интервалов')} "
            f"между {mrp.repairs_count} ремонтами)."
        ),
    ]
    if mrp.worse_count:
        parts.append(
            f"После {mrp.worse_count} из {mrp.repairs_count} ремонтов МРП сократился "
            "(ремонт можно считать неэффективным).",
        )

    current = next((r for r in mrp.repairs if r.repair_id == repair_id), None)
    if current is not None:
        if current.before_days is not None and current.after_days is not None:
            delta = current.after_days - current.before_days
            parts.append(
                f"Текущий ремонт: МРП до него {current.before_days:.0f} сут, после "
                f"{current.after_days:.0f} сут — "
                + ("сократился на" if delta < 0 else "вырос на")
                + f" {abs(delta):.0f} сут.",
            )
        elif current.before_days is not None and current.running_days is not None:
            state = {
                "better": "уже не меньше прежнего МРП",
                "watching": "пока меньше прежнего МРП, рано делать выводы",
            }.get(current.verdict, "оценка невозможна")
            parts.append(
                f"Текущий ремонт: до него МРП {current.before_days:.0f} сут, после "
                f"скважина работает {current.running_days:.0f} сут — {state}.",
            )
    return " ".join(parts)


# ---------------------------------------------------------------------------
# МРП по методике КМГ
# ---------------------------------------------------------------------------

Interval = tuple[datetime, datetime]


@dataclass(frozen=True, slots=True)
class WellFundInput:
    well_id: int
    name: str
    oil_field: str
    lift_type: str | None
    # Интервалы статуса «в работе» (WRK), местное время
    work_intervals: Sequence[Interval]
    # Ремонты скважины, пересекающие окно (для вычета времени и подсчёта)
    repairs: Sequence[RepairSpan]
    # Месяцы (год, месяц), в которых скважина входит в действующий фонд: на
    # 1-е число действует режим с плановым дебитом жидкости > 0. None — фонд не
    # ограничивается (месяц с работой в статусах).
    fund_months: frozenset[tuple[int, int]] | None = None


@dataclass(frozen=True, slots=True)
class KmgGroup:
    key: str
    wells: int
    avg_fund: float  # среднее за месяцы число скважин с работой в месяце
    calendar_days: float  # Σ по месяцам (фонд месяца × дней в месяце)
    work_days: float
    repairs: int
    gtm: int
    failures: int
    mrp_with_gtm: float | None
    mrp_without_gtm: float | None
    tech_reason: int = 0  # ремонты «по технической причине» (не входят в отказы)


@dataclass(frozen=True, slots=True)
class CrfWell:
    name: str
    oil_field: str
    repairs: int


@dataclass(frozen=True, slots=True)
class CrfResult:
    min_repairs: int
    fund_at_end: int
    wells: list[CrfWell]
    repairs: int
    percent_of_fund: float | None
    # число ремонтов без ГТМ за период → число скважин
    distribution: dict[int, int]
    # число ремонтов → имена скважин (только для ЧРФ: от min_repairs)
    wells_by_count: dict[int, list[str]]
    # ЧРФ по фонду ШГН (методика ЧРФ, шаг 6): скважины ШГН, их ремонты, доля от
    # фонда ШГН на конец окна
    shgn_fund_at_end: int = 0
    shgn_wells: int = 0
    shgn_repairs: int = 0
    shgn_percent_of_fund: float | None = None


@dataclass(frozen=True, slots=True)
class KmgResult:
    window_start: datetime
    window_end: datetime  # конец окна, не включая
    total: KmgGroup
    by_oil_field: list[KmgGroup]
    by_lift_type: list[KmgGroup]
    crf: CrfResult


def window_months(
    end_year: int,
    end_month: int,
    count: int = 12,
) -> list[tuple[int, int]]:
    """``count`` месяцев, последний — (end_year, end_month); месяц 1..12."""
    index = end_year * 12 + (end_month - 1)
    return [((i // 12), (i % 12) + 1) for i in range(index - count + 1, index + 1)]


def month_start(year: int, month: int) -> datetime:
    return datetime(year, month, 1)  # noqa: DTZ001 — наивное местное время, как в БД


def next_month(year: int, month: int) -> datetime:
    return month_start(year + month // 12, month % 12 + 1)


def _clip(interval: Interval, lo: datetime, hi: datetime) -> Interval | None:
    start, end = max(interval[0], lo), min(interval[1], hi)
    return (start, end) if end > start else None


def _union(intervals: Iterable[Interval]) -> list[Interval]:
    result: list[Interval] = []
    for start, end in sorted(intervals):
        if result and start <= result[-1][1]:
            result[-1] = (result[-1][0], max(result[-1][1], end))
        else:
            result.append((start, end))
    return result


def _subtract(base: list[Interval], cut: list[Interval]) -> list[Interval]:
    """``base`` без частей, покрытых ``cut`` (оба — непересекающиеся, отсортированы)."""
    result: list[Interval] = []
    for start, end in base:
        cursor = start
        for c_start, c_end in cut:
            if c_end <= cursor or c_start >= end:
                continue
            if c_start > cursor:
                result.append((cursor, c_start))
            cursor = max(cursor, c_end)
            if cursor >= end:
                break
        if cursor < end:
            result.append((cursor, end))
    return result


def _length_days(intervals: Iterable[Interval]) -> float:
    return sum(_days(end - start) for start, end in intervals)


def is_shgn(lift_type: str | None) -> bool:
    """Фонд ШГН: штанговые насосы (ШГН, САУ/ИСУ ШГН), без фонтана и комбинированных."""
    if not lift_type:
        return False
    name = lift_type.upper()
    return "ШГН" in name and not name.startswith(("ФОНТАН", "КОМБИНИР"))


def _in_fund(well: WellFundInput, year: int, month: int) -> bool:
    return well.fund_months is None or (year, month) in well.fund_months


def _group(
    key: str,
    wells: Sequence[WellFundInput],
    months: list[tuple[int, int]],
    *,
    now: datetime,
) -> KmgGroup:
    win_start = month_start(*months[0])
    win_end = next_month(*months[-1])

    work_days = 0.0
    calendar_days = 0.0
    fund_sum = 0
    repairs = 0
    gtm = 0
    tech = 0

    month_bounds = [(month_start(y, m), next_month(y, m)) for y, m in months]

    for well in wells:
        repair_cut = _union(
            clipped
            for r in well.repairs
            if (clipped := _clip((r.start, r.end or now), win_start, win_end))
        )
        work = _union(
            clipped
            for iv in well.work_intervals
            if (clipped := _clip(iv, win_start, win_end))
        )
        work = _subtract(work, repair_cut)

        for (year, month), (lo, hi) in zip(months, month_bounds, strict=True):
            if not _in_fund(well, year, month):
                continue
            days = _length_days(c for iv in work if (c := _clip(iv, lo, hi)))
            work_days += days
            # Без режима фонд — месяцы с работой; с режимом — все месяцы в фонде
            if days > 0 or well.fund_months is not None:
                fund_sum += 1
                calendar_days += _days(hi - lo)

        for r in well.repairs:
            if win_start <= r.start < win_end and _in_fund(
                well,
                r.start.year,
                r.start.month,
            ):
                repairs += 1
                gtm += r.is_gtm
                tech += r.is_tech and not r.is_gtm

    failures = repairs - gtm - tech
    return KmgGroup(
        key=key,
        wells=len(wells),
        avg_fund=fund_sum / len(months),
        calendar_days=calendar_days,
        work_days=work_days,
        repairs=repairs,
        gtm=gtm,
        failures=failures,
        mrp_with_gtm=_mrp(work_days, repairs),
        mrp_without_gtm=_mrp(work_days, failures),
        tech_reason=tech,
    )


def compute_kmg(  # noqa: PLR0913
    wells: Sequence[WellFundInput],
    *,
    end_year: int,
    end_month: int,
    months: int = 12,
    crf_min_repairs: int = 3,
    now: datetime,
) -> KmgResult:
    """Показатели МРП по методике КМГ и ЧРФ за скользящий период."""
    periods = window_months(end_year, end_month, months)
    win_start = month_start(*periods[0])
    win_end = next_month(*periods[-1])

    by_field: dict[str, list[WellFundInput]] = defaultdict(list)
    by_lift: dict[str, list[WellFundInput]] = defaultdict(list)
    for well in wells:
        by_field[well.oil_field].append(well)
        by_lift[well.lift_type or "не указан"].append(well)

    def groups(source: dict[str, list[WellFundInput]]) -> list[KmgGroup]:
        built = [_group(k, v, periods, now=now) for k, v in source.items()]
        return sorted(
            (g for g in built if g.avg_fund > 0 or g.repairs > 0),
            key=lambda g: (-g.avg_fund, g.key),
        )

    # ЧРФ: ремонты без ГТМ, начатые в окне; фонд — скважины с работой в последнем месяце
    last_lo = month_start(*periods[-1])
    last_y, last_m = periods[-1]

    def in_last_month_fund(well: WellFundInput) -> bool:
        if well.fund_months is not None:
            return (last_y, last_m) in well.fund_months
        return any(_clip(iv, last_lo, win_end) for iv in well.work_intervals)

    fund_wells = [w for w in wells if in_last_month_fund(w)]
    fund_at_end = len(fund_wells)
    shgn_fund_at_end = sum(1 for w in fund_wells if is_shgn(w.lift_type))
    counts: dict[str, int] = {}
    for well in wells:
        n = sum(
            1
            for r in well.repairs
            if win_start <= r.start < win_end
            and not r.is_gtm
            and _in_fund(well, r.start.year, r.start.month)
        )
        if n:
            counts[well.name] = n

    field_of = {w.name: w.oil_field for w in wells}
    lift_of = {w.name: w.lift_type for w in wells}
    crf_wells = sorted(
        (
            CrfWell(name, field_of[name], n)
            for name, n in counts.items()
            if n >= crf_min_repairs
        ),
        key=lambda c: (-c.repairs, c.name),
    )
    shgn_crf = [c for c in crf_wells if is_shgn(lift_of[c.name])]
    distribution: dict[int, int] = defaultdict(int)
    wells_by_count: dict[int, list[str]] = defaultdict(list)
    for name, n in counts.items():
        distribution[n] += 1
        if n >= crf_min_repairs:
            wells_by_count[n].append(name)

    return KmgResult(
        window_start=win_start,
        window_end=win_end,
        total=_group("НГДУ", wells, periods, now=now),
        by_oil_field=groups(by_field),
        by_lift_type=groups(by_lift),
        crf=CrfResult(
            min_repairs=crf_min_repairs,
            fund_at_end=fund_at_end,
            wells=crf_wells,
            repairs=sum(c.repairs for c in crf_wells),
            percent_of_fund=(len(crf_wells) / fund_at_end * 100)
            if fund_at_end
            else None,
            distribution=dict(sorted(distribution.items())),
            wells_by_count={k: sorted(v) for k, v in sorted(wells_by_count.items())},
            shgn_fund_at_end=shgn_fund_at_end,
            shgn_wells=len(shgn_crf),
            shgn_repairs=sum(c.repairs for c in shgn_crf),
            shgn_percent_of_fund=(len(shgn_crf) / shgn_fund_at_end * 100)
            if shgn_fund_at_end
            else None,
        ),
    )


# ---------------------------------------------------------------------------
# Динамика МРП по месяцам
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class MonthStat:
    """Сырые показатели группы скважин за один календарный месяц."""

    year: int
    month: int
    fund: int  # скважин с работой в месяце
    calendar_days: float  # фонд × дней в месяце
    work_days: float
    repairs: int
    gtm: int
    tech: int = 0


@dataclass(frozen=True, slots=True)
class DynamicsPoint:
    """Месяц ряда динамики: показатели месяца и скользящего периода по него."""

    month: str  # YYYY-MM
    # Текущий месяц (строки «Текущий месяц» формы КМГ)
    month_fund: int
    month_work_days: float
    month_repairs: int
    month_gtm: int
    month_mrp_with_gtm: float | None
    month_mrp_without_gtm: float | None
    # Скользящий период, заканчивающийся этим месяцем (строки «Скользящий год»)
    rolling_avg_fund: float
    rolling_work_days: float
    rolling_repairs: int
    rolling_gtm: int
    rolling_mrp_with_gtm: float | None
    rolling_mrp_without_gtm: float | None
    month_tech: int = 0
    rolling_tech: int = 0


def _mrp(work_days: float, count: int) -> float | None:
    """МРП по методике: T / R; если ремонтов не было — МРП равен времени работы T.

    Нет времени работы — МРП не определён (а не 0 сут).
    """
    if work_days <= 0:
        return None
    return work_days / count if count else work_days


def compute_month_stats(
    wells: Sequence[WellFundInput],
    months: list[tuple[int, int]],
    *,
    now: datetime,
) -> list[MonthStat]:
    """Показатели месяцев подряд за один проход по скважинам."""
    span_start = month_start(*months[0])
    span_end = next_month(*months[-1])
    bounds = [(month_start(y, m), next_month(y, m)) for y, m in months]

    fund = [0] * len(months)
    calendar = [0.0] * len(months)
    work_days = [0.0] * len(months)
    repairs = [0] * len(months)
    gtm = [0] * len(months)
    tech = [0] * len(months)

    for well in wells:
        repair_cut = _union(
            clipped
            for r in well.repairs
            if (clipped := _clip((r.start, r.end or now), span_start, span_end))
        )
        work = _subtract(
            _union(
                clipped
                for iv in well.work_intervals
                if (clipped := _clip(iv, span_start, span_end))
            ),
            repair_cut,
        )
        for i, (lo, hi) in enumerate(bounds):
            if not _in_fund(well, *months[i]):
                continue
            days = _length_days(c for iv in work if (c := _clip(iv, lo, hi)))
            work_days[i] += days
            if days > 0 or well.fund_months is not None:
                fund[i] += 1
                calendar[i] += _days(hi - lo)
        for r in well.repairs:
            for i, (lo, hi) in enumerate(bounds):
                if lo <= r.start < hi:
                    if _in_fund(well, *months[i]):
                        repairs[i] += 1
                        gtm[i] += r.is_gtm
                        tech[i] += r.is_tech and not r.is_gtm
                    break

    return [
        MonthStat(y, m, fund[i], calendar[i], work_days[i], repairs[i], gtm[i], tech[i])
        for i, (y, m) in enumerate(months)
    ]


def compute_kmg_dynamics(  # noqa: PLR0913
    wells: Sequence[WellFundInput],
    *,
    end_year: int,
    end_month: int,
    points: int = 12,
    window_months_count: int = 12,
    now: datetime,
) -> list[DynamicsPoint]:
    """Ряд МРП по методике КМГ: ``points`` месяцев, последний — ``end_month``.

    Для каждого месяца — МРП самого месяца и МРП скользящего периода из
    ``window_months_count`` месяцев, заканчивающегося им. Последняя точка
    совпадает с ``compute_kmg`` за то же окно.
    """
    span = window_months(end_year, end_month, points + window_months_count - 1)
    stats = compute_month_stats(wells, span, now=now)

    series: list[DynamicsPoint] = []
    for i in range(points):
        window = stats[i : i + window_months_count]
        current = stats[i + window_months_count - 1]
        work = sum(s.work_days for s in window)
        repairs = sum(s.repairs for s in window)
        gtm = sum(s.gtm for s in window)
        tech = sum(s.tech for s in window)
        series.append(
            DynamicsPoint(
                month=f"{current.year}-{current.month:02d}",
                month_fund=current.fund,
                month_work_days=current.work_days,
                month_repairs=current.repairs,
                month_gtm=current.gtm,
                month_mrp_with_gtm=_mrp(current.work_days, current.repairs),
                month_mrp_without_gtm=_mrp(
                    current.work_days,
                    current.repairs - current.gtm - current.tech,
                ),
                month_tech=current.tech,
                rolling_tech=tech,
                rolling_avg_fund=sum(s.fund for s in window) / window_months_count,
                rolling_work_days=work,
                rolling_repairs=repairs,
                rolling_gtm=gtm,
                rolling_mrp_with_gtm=_mrp(work, repairs),
                rolling_mrp_without_gtm=_mrp(work, repairs - gtm - tech),
            ),
        )
    return series
