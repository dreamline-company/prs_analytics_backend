"""Профиль добычи за период: план, факт с учётом простоев, точки графика.

Решение 30.10.2026 (docs/production_profile_periods_plan.md). Периоды: сутки
и 3 дня — точки по часу, неделя и месяц (с 1-го числа, как месячный план
техрежима) — по суткам; последняя точка неполная, до текущего момента.

Точка графика:

- дебит — медиана трёх последних замеров ЦИТС в работе (есть жидкость и
  нефть) на конец точки, глубина 60 суток: в ЦИТС бывают выбросы в 2–4 раза
  выше соседних замеров, медиана трёх их гасит, а настоящее изменение дебита
  линия подхватывает со второго замера;
- доля работы — по часам: доля отсчётов СДМО с работающим приводом; без СДМО
  или если станция в этот час молчала — по статусам ABAI (WRK / PEXP); сутки
  собираются из часов; текущий час, по которому СДМО ещё не догрузилось
  (загрузчик раз в 5 минут), — «данных ещё нет», а не ABAI;
- факт = дебит × доля работы; простой всю точку — 0, даже без дебита;
- план — нефть техрежима, действующего на дату точки (как в сводках).

Итоги за период — средние по точкам с весом длительности, т. е. добыча за
период ÷ его длительность. Нет ни одного замера в работе за период — факта
нет («Замера нет»): старые замеры его не подменяют; исключение — привод
стоял всё время, по которому есть данные о работе, тогда факт 0.
"""

import math
from bisect import bisect_left
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from statistics import median
from typing import Literal

from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.drive import (
    DRIVE_RUNNING_VLT_CODES,
    DRIVE_STOPPED_VLT_CODES,
)
from apps.wells.dto.internal.well_production_profile import ProfilePeriod
from apps.wells.repositories import WellStatusRepository

RATE_MEASUREMENTS = 3
# Глубина поиска замеров для дебита первых точек периода.
RATE_LOOKBACK = timedelta(days=60)
# Меньше пригодных отсчётов СДМО за час — доля по ним ненадёжна, берётся ABAI;
# у неполного часа порог пропорционально меньше.
MIN_SDMO_SAMPLES_PER_HOUR = 3
HOUR = timedelta(hours=1)
# Режимы месячные и приезжают из ABAI с лагом: пока нового нет, план — по
# прошлому (как PLAN_GRACE_DAYS сводок и компенсации).
PLAN_GRACE = timedelta(days=31)
WORKING_STATUS_CODES = frozenset({"WRK", "PEXP"})

SOURCE_SDMO = "sdmo"
SOURCE_ABAI = "abai"
SOURCE_MIXED = "mixed"

type Step = Literal["hour", "day"]


@dataclass(frozen=True, slots=True)
class Measurement:
    time: datetime
    oil: float | None
    liquid: float | None


@dataclass(frozen=True, slots=True)
class Bucket:
    start: datetime
    end: datetime

    @property
    def seconds(self) -> float:
        return (self.end - self.start).total_seconds()


@dataclass(frozen=True, slots=True)
class WorkShare:
    share: float  # 0…1
    source: str  # sdmo / abai / mixed (сутки из часов разных источников)


@dataclass(frozen=True, slots=True)
class PlanRegime:
    start: date
    end: date
    oil: float | None


@dataclass(frozen=True, slots=True)
class ProfilePoint:
    time: datetime  # начало часа или суток
    plan_oil: float | None
    fact_oil: float | None  # None — разрыв на графике
    oil_rate: float | None  # дебит в работе (медиана трёх замеров)
    work_share: float | None


@dataclass(frozen=True, slots=True)
class ProductionProfile:
    period: ProfilePeriod
    step: Step
    period_from: datetime
    period_to: datetime
    plan_oil: float | None
    fact_oil: float | None  # None — «Замера нет» или доли работы не узнать
    oil_rate_working: float | None  # средний дебит, пока скважина работала
    has_measurements: bool  # есть замер в работе за период
    work_share: float | None
    work_share_source: str | None  # sdmo / abai / mixed
    measurements_count: int  # замеров в работе за период
    points: list[ProfilePoint]
    measurements: list[Measurement]  # все замеры ЦИТС за период


def _floor_hour(moment: datetime) -> datetime:
    return moment.replace(minute=0, second=0, microsecond=0)


def _floor_day(moment: datetime) -> datetime:
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def period_buckets(period: ProfilePeriod, now: datetime) -> tuple[Step, list[Bucket]]:
    """Точки периода по порядку; последняя заканчивается в ``now``.

    Ровно на границе часа (суток) текущая точка пустая — тогда последней
    считается прошедшая, чтобы в периоде было полное число точек.
    """
    if period in (ProfilePeriod.DAY, ProfilePeriod.THREE_DAYS):
        step: Step = "hour"
        unit = timedelta(hours=1)
        last = _floor_hour(now)
        count = 24 if period is ProfilePeriod.DAY else 72
    else:
        step = "day"
        unit = timedelta(days=1)
        last = _floor_day(now)
        count = 7
    if last == now:
        last -= unit
    if period is ProfilePeriod.MONTH:
        count = (last - last.replace(day=1)).days + 1
    first = last - unit * (count - 1)
    buckets = [
        Bucket(start, min(start + unit, now))
        for start in (first + unit * index for index in range(count))
    ]
    return step, buckets


def working_measurements(measurements: Iterable[Measurement]) -> list[Measurement]:
    """Замеры в работе: есть жидкость и нефть; дубль (та же минута) — один."""
    by_minute = {
        item.time.replace(second=0, microsecond=0): item
        for item in measurements
        if item.liquid is not None and item.liquid > 0 and item.oil is not None
    }
    return [by_minute[moment] for moment in sorted(by_minute)]


def rate_before(working: Sequence[Measurement], moment: datetime) -> float | None:
    """Медиана нефти трёх последних замеров в работе раньше ``moment``."""
    index = bisect_left([item.time for item in working], moment)
    recent = [
        item.oil
        for item in working[max(index - RATE_MEASUREMENTS, 0) : index]
        if item.oil is not None
    ]
    return median(recent) if recent else None


def plan_on(regimes: Sequence[PlanRegime], day: date) -> float | None:
    """Нефть режима, действующего на дату: последний начавшийся, закончился
    не раньше чем ``PLAN_GRACE`` назад. ``regimes`` — по (start_date, id): из
    режимов с одной датой начала берётся последний, как в карточке и сводках."""
    current = None
    for regime in regimes:
        in_force = regime.start <= day and regime.end >= day - PLAN_GRACE
        if in_force and (current is None or regime.start >= current.start):
            current = regime
    return current.oil if current is not None else None


def abai_work_share(
    intervals: Iterable[tuple],
    *,
    since: datetime,
    until: datetime,
) -> float | None:
    """Доля времени в статусах работы ABAI за [since, until) — от времени,
    покрытого статусами; статусов нет вовсе — None (неизвестно, а не 0 %)."""
    covered = working = 0.0
    for _well, dbeg, dend, code, _name, _reason in intervals:
        start, end = max(dbeg, since), min(dend, until)
        if end <= start:
            continue
        seconds = (end - start).total_seconds()
        covered += seconds
        if code in WORKING_STATUS_CODES:
            working += seconds
    if covered <= 0:
        return None
    return min(working / covered, 1.0)


def hour_buckets(since: datetime, until: datetime) -> list[Bucket]:
    """Часы [since, until), ``since`` — начало часа; последний — до ``until``."""
    hours = []
    start = since
    while start < until:
        hours.append(Bucket(start, min(start + HOUR, until)))
        start += HOUR
    return hours


def hourly_work_shares(
    hours: Sequence[Bucket],
    *,
    sdmo_counts: Mapping[datetime, tuple[int, int]],
    abai_intervals: Sequence[tuple],
) -> list[WorkShare | None]:
    """Доля работы по часам: СДМО, если отсчётов хватает, иначе ABAI.

    Текущий неполный час, по которому отсчётов СДМО ещё нет, хотя в прошлом
    часе они были, — None: загрузчик отстаёт, станция не молчит, и ABAI
    (который сам отстаёт от остановок) подменять её не должен.
    """
    shares: list[WorkShare | None] = []
    for index, hour in enumerate(hours):
        usable, running = sdmo_counts.get(hour.start, (0, 0))
        needed = max(1, math.ceil(MIN_SDMO_SAMPLES_PER_HOUR * hour.seconds / 3600))
        if usable >= needed:
            shares.append(WorkShare(running / usable, SOURCE_SDMO))
            continue
        previous = shares[-1] if shares else None
        if (
            index == len(hours) - 1
            and hour.seconds < HOUR.total_seconds()
            and previous is not None
            and previous.source == SOURCE_SDMO
        ):
            shares.append(None)
            continue
        share = abai_work_share(abai_intervals, since=hour.start, until=hour.end)
        shares.append(WorkShare(share, SOURCE_ABAI) if share is not None else None)
    return shares


def combine_work_shares(
    parts: Iterable[tuple[Bucket, WorkShare | None]],
) -> WorkShare | None:
    """Доля работы за сутки из часов с известной долей, с весом длительности."""
    known = [(bucket, share) for bucket, share in parts if share is not None]
    seconds = sum(bucket.seconds for bucket, _share in known)
    if seconds <= 0:
        return None
    share = sum(share.share * bucket.seconds for bucket, share in known) / seconds
    sources = {share.source for _bucket, share in known}
    return WorkShare(share, sources.pop() if len(sources) == 1 else SOURCE_MIXED)


def point_work_shares(
    buckets: Sequence[Bucket],
    hours: Sequence[Bucket],
    hourly: Sequence[WorkShare | None],
) -> list[WorkShare | None]:
    """Доли работы точек периода из часовых долей."""
    return [
        combine_work_shares(
            (hour, share)
            for hour, share in zip(hours, hourly, strict=True)
            if bucket.start <= hour.start < bucket.end
        )
        for bucket in buckets
    ]


def _weighted(pairs: Iterable[tuple[float | None, float]]) -> float | None:
    total = weight = 0.0
    for value, seconds in pairs:
        if value is None:
            continue
        total += value * seconds
        weight += seconds
    return total / weight if weight > 0 else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def build_profile(
    period: ProfilePeriod,
    *,
    now: datetime,
    measurements: Sequence[Measurement],
    work_shares: Sequence[WorkShare | None],
    regimes: Sequence[PlanRegime],
) -> ProductionProfile:
    """``measurements`` — замеры с запасом ``RATE_LOOKBACK`` до начала
    периода; ``work_shares`` — по точкам ``period_buckets(period, now)``."""
    step, buckets = period_buckets(period, now)
    period_from = buckets[0].start
    working = working_measurements(item for item in measurements if item.time < now)
    in_period = [item for item in working if item.time >= period_from]
    # Простой — по известным долям: текущие минуты, по которым СДМО ещё не
    # догрузилось, «стоял весь период» не отменяют.
    known_shares = [share for share in work_shares if share is not None]
    stood_all = bool(known_shares) and all(share.share == 0 for share in known_shares)
    show_fact = bool(in_period) or stood_all

    points: list[ProfilePoint] = []
    plans: list[tuple[float | None, float]] = []
    facts: list[tuple[float | None, float]] = []
    shares: list[tuple[float | None, float]] = []
    working_rates: list[tuple[float | None, float]] = []
    sources: set[str] = set()
    for bucket, share in zip(buckets, work_shares, strict=True):
        # «Замера нет» — и дебит по старым замерам не показываем.
        rate = rate_before(working, bucket.end) if show_fact else None
        plan = plan_on(regimes, bucket.start.date())
        if not show_fact:
            fact = None
        elif share is not None and share.share == 0:
            fact = 0.0
        elif share is None or rate is None:
            fact = None
        else:
            fact = rate * share.share
        plans.append((plan, bucket.seconds))
        facts.append((fact, bucket.seconds))
        if share is not None:
            shares.append((share.share, bucket.seconds))
            working_rates.append((rate, share.share * bucket.seconds))
            sources.add(share.source)
        points.append(
            ProfilePoint(
                time=bucket.start,
                plan_oil=_round(plan),
                fact_oil=_round(fact),
                oil_rate=_round(rate),
                work_share=_round(share.share, 3) if share is not None else None,
            ),
        )

    source = SOURCE_MIXED if len(sources) > 1 else next(iter(sources), None)
    return ProductionProfile(
        period=period,
        step=step,
        period_from=period_from,
        period_to=now,
        plan_oil=_round(_weighted(plans)),
        fact_oil=_round(_weighted(facts)),
        # Средний дебит, пока скважина работала: вес — время работы.
        oil_rate_working=_round(_weighted(working_rates)) if in_period else None,
        has_measurements=bool(in_period),
        work_share=_round(_weighted(shares), 3),
        work_share_source=source,
        measurements_count=len(in_period),
        points=points,
        measurements=[item for item in measurements if period_from <= item.time < now],
    )


class WellProductionProfileService:
    def __init__(
        self,
        *,
        telemetry_repository: TelemetryRepository,
        tech_regime_repository: TechRegimeRepository,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        well_status_repository: WellStatusRepository,
    ) -> None:
        self.telemetry_repository = telemetry_repository
        self.tech_regime_repository = tech_regime_repository
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.well_status_repository = well_status_repository

    async def get(
        self,
        *,
        well_id: int,
        abai_well_id: int,
        period: ProfilePeriod,
        now: datetime,
    ) -> ProductionProfile:
        """``now`` — местное наивное время, как в замерах, СДМО и ABAI."""
        _step, buckets = period_buckets(period, now)
        since = buckets[0].start
        rows = await self.telemetry_repository.list_by_well_id_in_period(
            well_id,
            date_time_from=since - RATE_LOOKBACK,
            date_time_to=now,
        )
        measurements = [
            Measurement(time=row.date_time, oil=row.qm_oil, liquid=row.qv_liquid)
            for row in rows
        ]
        # Все станции скважины, и неактивные: доля работы — по отсчётам за
        # период; сменили станцию — складывается из обеих.
        station_ids = [
            station.id
            for station in await self.sdmo_station_repository.list_by_well_id(
                well_id,
            )
        ]
        # Час до периода — чтобы отличить отставание загрузчика СДМО в первом
        # часе периода от молчания станции.
        hours = hour_buckets(since - HOUR, now)
        sdmo_counts = await self.sdmo_fc_data_repository.drive_running_by_hour(
            station_ids,
            since=hours[0].start,
            until=now,
            running_codes=sorted(DRIVE_RUNNING_VLT_CODES),
            stopped_codes=sorted(DRIVE_STOPPED_VLT_CODES),
        )
        intervals = await self.well_status_repository.list_intervals_by_abai_well_ids(
            [abai_well_id],
            since=hours[0].start,
            until=now,
        )
        hourly = hourly_work_shares(
            hours,
            sdmo_counts=sdmo_counts,
            abai_intervals=intervals,
        )
        regimes = await self.tech_regime_repository.list_by_abai_well_id_in_period(
            abai_well_id,
            start_date_to=now.date(),
        )
        return build_profile(
            period,
            now=now,
            measurements=measurements,
            work_shares=point_work_shares(buckets, hours[1:], hourly[1:]),
            regimes=[
                PlanRegime(start=row.start_date, end=row.end_date, oil=row.oil)
                for row in regimes
            ],
        )
