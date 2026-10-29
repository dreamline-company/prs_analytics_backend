"""Факт нефти с начала месяца — с учётом простоев (решение 30.10.2026).

План в техрежиме месячный, поэтому факт для «план / факт» тоже за месяц:

- дебит в работе — медиана нефти по замерам ЦИТС с 1-го числа, где скважина
  давала жидкость (в ЦИТС бывают выбросы — нефть в 2–4 раза выше соседних
  замеров, — среднее они завышают, медиану почти нет); дубли замера
  (одна минута) считаются один раз; если в месяце меньше трёх таких замеров
  (начало месяца, редкие замеры), берутся три последних, даже из прошлого
  месяца;
- доля времени работы с 1-го числа — по приводу СДМО (доля отсчётов, где
  привод работал), у скважин без СДМО — по статусам ABAI (WRK / PEXP);
- факт = дебит в работе × доля работы: скважина, простоявшая полмесяца,
  показывает около половины дебита, а не 100 % и не 0 %.
"""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import median

from apps.telemetry.repositories.sdmo import SdmoFcDataRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.drive import (
    DRIVE_RUNNING_VLT_CODES,
    DRIVE_STOPPED_VLT_CODES,
)
from apps.wells.repositories import WellStatusRepository

MIN_MEASUREMENTS = 3
# Глубина поиска трёх последних замеров, если в месяце их меньше.
FALLBACK_WINDOW = timedelta(days=60)
# Меньше пригодных отсчётов СДМО за месяц — доля по ним ненадёжна, берётся ABAI.
MIN_SDMO_SAMPLES = 12
WORKING_STATUS_CODES = frozenset({"WRK", "PEXP"})

SOURCE_SDMO = "sdmo"
SOURCE_ABAI = "abai"


@dataclass(frozen=True, slots=True)
class MonthMeasurement:
    time: datetime
    oil: float | None
    liquid: float | None


@dataclass(frozen=True, slots=True)
class MonthOilFact:
    oil_rate: float | None  # с учётом простоев; None — долю работы не узнать
    oil_rate_working: float | None  # медиана замеров в работе
    work_share: float | None  # 0…1 с начала месяца
    work_share_source: str | None  # sdmo / abai
    measurements: int  # сколько замеров в медиане
    period_from: datetime | None  # первый замер медианы


def month_start(now: datetime) -> datetime:
    return now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def _working(measurements: Iterable[MonthMeasurement]) -> list[MonthMeasurement]:
    """Замеры в работе: есть жидкость и нефть; по одному на минуту."""
    by_minute = {
        item.time.replace(second=0, microsecond=0): item
        for item in measurements
        if item.liquid is not None and item.liquid > 0 and item.oil is not None
    }
    return [by_minute[moment] for moment in sorted(by_minute)]


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


def month_oil_fact(
    measurements: Sequence[MonthMeasurement],
    *,
    now: datetime,
    work_share: float | None,
    work_share_source: str | None,
) -> MonthOilFact:
    working = _working(measurements)
    in_month = [item for item in working if item.time >= month_start(now)]
    used = (
        in_month if len(in_month) >= MIN_MEASUREMENTS else working[-MIN_MEASUREMENTS:]
    )
    if not used:
        return MonthOilFact(None, None, work_share, work_share_source, 0, None)
    rate = median(item.oil for item in used)
    return MonthOilFact(
        oil_rate=round(rate * work_share, 2) if work_share is not None else None,
        oil_rate_working=round(rate, 2),
        work_share=round(work_share, 3) if work_share is not None else None,
        work_share_source=work_share_source,
        measurements=len(used),
        period_from=used[0].time,
    )


class WellMonthProductionService:
    def __init__(
        self,
        *,
        telemetry_repository: TelemetryRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        well_status_repository: WellStatusRepository,
    ) -> None:
        self.telemetry_repository = telemetry_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.well_status_repository = well_status_repository

    async def get_oil_fact(
        self,
        *,
        well_id: int,
        abai_well_id: int,
        station_id: int | None,
        now: datetime,
    ) -> MonthOilFact:
        """``station_id`` — станция СДМО, по которой считать долю работы
        (та, что дала последний отсчёт); None — у скважины нет СДМО."""
        rows = await self.telemetry_repository.list_by_well_id_in_period(
            well_id,
            date_time_from=min(month_start(now), now - FALLBACK_WINDOW),
        )
        measurements = [
            MonthMeasurement(time=row.date_time, oil=row.qm_oil, liquid=row.qv_liquid)
            for row in rows
        ]
        share, source = await self._work_share(
            abai_well_id=abai_well_id,
            station_id=station_id,
            now=now,
        )
        return month_oil_fact(
            measurements,
            now=now,
            work_share=share,
            work_share_source=source,
        )

    async def _work_share(
        self,
        *,
        abai_well_id: int,
        station_id: int | None,
        now: datetime,
    ) -> tuple[float | None, str | None]:
        since = month_start(now)
        if since >= now:
            return None, None
        if station_id is not None:
            usable, running = await self.sdmo_fc_data_repository.count_drive_running(
                station_id,
                since=since,
                until=now,
                running_codes=sorted(DRIVE_RUNNING_VLT_CODES),
                stopped_codes=sorted(DRIVE_STOPPED_VLT_CODES),
            )
            if usable >= MIN_SDMO_SAMPLES:
                return running / usable, SOURCE_SDMO
        intervals = await self.well_status_repository.list_intervals_by_abai_well_ids(
            [abai_well_id],
            since=since,
            until=now,
        )
        share = abai_work_share(intervals, since=since, until=now)
        return share, SOURCE_ABAI if share is not None else None
