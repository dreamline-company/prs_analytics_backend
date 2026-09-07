"""Бэктест правила R2 на всём флоте SDMO: сбор данных + матчинг эпизодов.

Ретро-тест качества правила (не продакшн ``run_for_well`` с 60-дн окном): для
каждой скважины загружаем полную историю корзин, ищем все эпизоды устойчивых
сработок, матчим с ремонтами по обрыву штанги из ``repairs_repair``.

FP-эпизоды бьём на 3 категории:
    - ``transient``: момент восстановился в течение 24ч (шум правила);
    - ``other_repair``: эпизод пересекается с ремонтом другого типа (корректная
      детекция остановки насоса, но причина — не обрыв);
    - ``no_repair``: устойчивый эпизод без пересечения с любым ремонтом
      (либо пропуск в БД ремонтов, либо необнаруженный отказ).
"""

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.detector import TARGET_TYPE_1900
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.rule import is_flagged
from apps.detectors.rod_breaks.services import bucketizer
from apps.detectors.rod_breaks.services.telemetry_source import (
    RodBreakTelemetrySource,
)
from apps.repairs.models.repair import Repair
from apps.telemetry.models.sdmo import SdmoFcData, SdmoStation
from apps.wells.models.well import Well

# Матчинг «эпизод ↔ ремонт»: fired_at ∈ [start - 3d, end + 3d].
MATCH_BEFORE_DAYS = 3
MATCH_AFTER_END_DAYS = 3
# Заглушка для ремонтов без end_time (спека Q3).
OPEN_REPAIR_DURATION = timedelta(days=1)
# Склейка близких серий в один эпизод (один физический обрыв = один эпизод).
MERGE_GAP_HOURS = 24
# Транзиент: момент восстановился до > RECOVER_RATIO * base_moment
# в течение RECOVER_HORIZON_BUCKETS после последней сработавшей корзины.
# 96 корзин × 0.25ч = 24ч горизонт восстановления (масштаб под config.BUCKET_HOURS).
RECOVER_HORIZON_BUCKETS = 96
RECOVER_RATIO = 0.5
# Полное окно бэктеста (детерминированные границы для repro).
BACKTEST_START = datetime(1970, 1, 1)  # noqa: DTZ001 — savetime хранится naive UTC
BACKTEST_END = datetime(2100, 1, 1)  # noqa: DTZ001


@dataclass(frozen=True, slots=True)
class RepairSpan:
    """Нормализованный ремонт: end_time уже подставлен заглушкой при NULL."""

    id: int
    start_time: datetime
    end_time: datetime
    original_end_time: datetime | None
    is_rod_break: bool
    work_list: str


@dataclass(frozen=True, slots=True)
class Episode:
    """Устойчивая серия сработок правила (склеенные соседние runs).

    ``fp_class`` = None означает TP; иначе одна из строк
    ``"transient" | "other_repair" | "no_repair"``.
    """

    fired_at: datetime
    end_ts: datetime
    persistent: bool
    fp_class: str | None
    matched_repair_id: int | None


@dataclass
class WellTarget:
    """Единица работы: скважина + выбранная целевая станция (или None)."""

    well_id: int
    well_name: str | None
    station_id: int | None
    station_code: str | None
    station_type_1900: int | None


@dataclass
class WellResult:
    """Результат прогона по одной скважине для отчёта."""

    target: WellTarget
    series: list[Bucket2h]
    base_moment: float | None
    coverage_start: datetime | None
    coverage_end: datetime | None
    rod_break_repairs: list[RepairSpan]
    rod_break_repairs_in_coverage: list[RepairSpan]
    other_repairs: list[RepairSpan]
    episodes: list[Episode] = field(default_factory=list)
    tp: int = 0
    fn: int = 0
    fp_transient: int = 0
    fp_other_repair: int = 0
    fp_no_repair: int = 0
    missed_repair_ids: list[int] = field(default_factory=list)
    skipped_reason: str | None = None

    @property
    def strict_precision(self) -> float | None:
        denom = self.tp + self.fp_transient + self.fp_other_repair + self.fp_no_repair
        return self.tp / denom if denom else None

    @property
    def soft_precision(self) -> float | None:
        """Precision без ``other_repair``-FP (корректные детекции остановок)."""
        denom = self.tp + self.fp_transient + self.fp_no_repair
        return self.tp / denom if denom else None

    @property
    def recall(self) -> float | None:
        """Доля ремонтов «обрыв» в покрытии, пойманных хотя бы одним эпизодом.

        Считается по РЕМОНТАМ, а не по TP-эпизодам: один ремонт могут поймать
        несколько эпизодов, а эпизод — совпасть с ремонтом вне покрытия, поэтому
        ``tp`` (счётчик эпизодов) в числитель recall брать нельзя. ``tp`` —
        только для precision.
        """
        total = len(self.rod_break_repairs_in_coverage)
        if not total:
            return None
        detected = total - len(self.missed_repair_ids)
        return detected / total


async def collect_targets(session: AsyncSession) -> list[WellTarget]:
    """Все скважины, у которых есть fc_data. Целевая станция — VLT (type=6),
    иначе первая попавшаяся (тогда прогон детектора пропускаем).
    """
    ids_stmt = select(SdmoFcData.station_id).distinct()
    ids_with_data = {row[0] for row in (await session.execute(ids_stmt)).all()}
    if not ids_with_data:
        return []

    stations_stmt = (
        select(SdmoStation)
        .where(SdmoStation.id.in_(ids_with_data))
        .where(SdmoStation.well_id.is_not(None))
    )
    stations = list((await session.execute(stations_stmt)).scalars().all())

    # На well выбираем VLT-станцию, если она есть.
    by_well: dict[int, SdmoStation] = {}
    for station in stations:
        current = by_well.get(station.well_id)
        if current is None:
            by_well[station.well_id] = station
            continue
        if (
            station.type_1900 == TARGET_TYPE_1900
            and current.type_1900 != TARGET_TYPE_1900
        ):
            by_well[station.well_id] = station

    well_ids = list(by_well.keys())
    wells_stmt = select(Well.id, Well.name).where(Well.id.in_(well_ids))
    well_names = {row[0]: row[1] for row in (await session.execute(wells_stmt)).all()}

    return [
        WellTarget(
            well_id=well_id,
            well_name=well_names.get(well_id),
            station_id=station.id,
            station_code=station.code,
            station_type_1900=station.type_1900,
        )
        for well_id, station in sorted(by_well.items())
    ]


async def load_repairs(
    session: AsyncSession,
    well_ids: Sequence[int],
) -> tuple[dict[int, list[RepairSpan]], dict[int, list[RepairSpan]]]:
    """Ремонты по обрыву штанги и все прочие ремонты, сгруппированные по well_id.

    Джойн — через ``Repair.abai_well_id == Well.abai_id`` (``well_id`` в
    ``repairs_repair`` nullable, ``abai_well_id`` — нет). Ключом словаря —
    наш ``Well.id``.
    """
    if not well_ids:
        return {}, {}

    stmt = (
        select(Repair, Well.id.label("resolved_well_id"))
        .join(Well, Well.abai_id == Repair.abai_well_id)
        .where(Well.id.in_(well_ids))
        .order_by(Repair.start_time)
    )
    rows = (await session.execute(stmt)).all()

    rod_break: dict[int, list[RepairSpan]] = {wid: [] for wid in well_ids}
    other: dict[int, list[RepairSpan]] = {wid: [] for wid in well_ids}
    for repair, resolved_well_id in rows:
        work_list = repair.work_list or ""
        is_break = "обрыв" in work_list.lower()
        end = repair.end_time or (repair.start_time + OPEN_REPAIR_DURATION)
        span = RepairSpan(
            id=repair.id,
            start_time=repair.start_time,
            end_time=end,
            original_end_time=repair.end_time,
            is_rod_break=is_break,
            work_list=work_list,
        )
        (rod_break if is_break else other)[resolved_well_id].append(span)
    return rod_break, other


def _find_episodes(
    series: Sequence[Bucket2h],
    base_moment: float | None,
) -> list[Episode]:
    """Найти эпизоды: run'ы флагов длиной ≥ SUSTAIN, склеенные в пределах 24ч.

    Каждому эпизоду приписывается ``persistent``: True если момент не
    восстановился до > RECOVER_RATIO * base_moment в течение
    RECOVER_HORIZON_BUCKETS после конца эпизода. FP-класс и matched_repair_id
    заполняются позже, в ``classify_episodes``.
    """
    flags = [is_flagged(bucket) for bucket in series]

    runs: list[tuple[int, int]] = []
    i, n = 0, len(flags)
    while i < n:
        if not flags[i]:
            i += 1
            continue
        j = i
        while j < n and flags[j]:
            j += 1
        if j - i >= config.SUSTAIN_BUCKETS:
            runs.append((i, j - 1))
        i = j

    merged: list[tuple[int, int]] = []
    for lo, hi in runs:
        if merged and (
            series[lo].start_ts - series[merged[-1][1]].start_ts
            <= timedelta(hours=MERGE_GAP_HOURS)
        ):
            merged[-1] = (merged[-1][0], hi)
        else:
            merged.append((lo, hi))

    return [
        Episode(
            fired_at=series[lo].start_ts,
            end_ts=series[hi].start_ts,
            persistent=_is_persistent(series, hi, base_moment),
            fp_class=None,
            matched_repair_id=None,
        )
        for lo, hi in merged
    ]


def _is_persistent(
    series: Sequence[Bucket2h],
    end_idx: int,
    base_moment: float | None,
) -> bool:
    """Momentum recovered above 50% of base within 24h after last flagged bucket?

    Если base не известен или <= 0 — считаем persistent (нет сигнала для отказа).
    """
    if base_moment is None or base_moment <= 0:
        return True
    ahead = series[end_idx + 1 : end_idx + 1 + RECOVER_HORIZON_BUCKETS]
    recovered = any(bucket.mom_min > RECOVER_RATIO * base_moment for bucket in ahead)
    return not recovered


def _overlaps(fired_at: datetime, repair: RepairSpan, before: timedelta) -> bool:
    return (
        repair.start_time - before
        <= fired_at
        <= repair.end_time + timedelta(days=MATCH_AFTER_END_DAYS)
    )


def _classify_episodes(
    episodes: list[Episode],
    rod_break_repairs: Sequence[RepairSpan],
    other_repairs: Sequence[RepairSpan],
) -> list[Episode]:
    """Приписать каждому эпизоду TP / FP-класс + matched_repair_id."""
    before = timedelta(days=MATCH_BEFORE_DAYS)
    classified: list[Episode] = []
    for ep in episodes:
        matched = next(
            (r for r in rod_break_repairs if _overlaps(ep.fired_at, r, before)),
            None,
        )
        if matched is not None:
            classified.append(
                Episode(
                    fired_at=ep.fired_at,
                    end_ts=ep.end_ts,
                    persistent=ep.persistent,
                    fp_class=None,
                    matched_repair_id=matched.id,
                ),
            )
            continue

        if not ep.persistent:
            fp_class = "transient"
        elif any(_overlaps(ep.fired_at, r, before) for r in other_repairs):
            fp_class = "other_repair"
        else:
            fp_class = "no_repair"
        classified.append(
            Episode(
                fired_at=ep.fired_at,
                end_ts=ep.end_ts,
                persistent=ep.persistent,
                fp_class=fp_class,
                matched_repair_id=None,
            ),
        )
    return classified


async def analyze_well(
    source: RodBreakTelemetrySource,
    target: WellTarget,
    rod_break_repairs: Sequence[RepairSpan],
    other_repairs: Sequence[RepairSpan],
) -> WellResult:
    """Загрузить историю, найти эпизоды, посчитать метрики. Один вызов на well."""
    if target.station_id is None or target.station_type_1900 != TARGET_TYPE_1900:
        return WellResult(
            target=target,
            series=[],
            base_moment=None,
            coverage_start=None,
            coverage_end=None,
            rod_break_repairs=list(rod_break_repairs),
            rod_break_repairs_in_coverage=[],
            other_repairs=list(other_repairs),
            skipped_reason="no_target_station",
        )

    raw = await source.load_raw_buckets(
        target.station_id,
        BACKTEST_START,
        BACKTEST_END,
    )
    if not raw:
        return WellResult(
            target=target,
            series=[],
            base_moment=None,
            coverage_start=None,
            coverage_end=None,
            rod_break_repairs=list(rod_break_repairs),
            rod_break_repairs_in_coverage=[],
            other_repairs=list(other_repairs),
            skipped_reason="no_data",
        )

    series = bucketizer.build_series(raw)
    base_moment = await source.get_base_moment(
        target.station_id,
        BACKTEST_START,
        BACKTEST_END,
    )

    coverage_start = series[0].start_ts
    coverage_end = series[-1].start_ts

    episodes = _classify_episodes(
        _find_episodes(series, base_moment),
        rod_break_repairs,
        other_repairs,
    )

    in_coverage = [
        r for r in rod_break_repairs if coverage_start <= r.start_time <= coverage_end
    ]
    matched_ids = {ep.matched_repair_id for ep in episodes if ep.matched_repair_id}
    missed = [r.id for r in in_coverage if r.id not in matched_ids]

    tp = sum(1 for ep in episodes if ep.fp_class is None)
    fp_transient = sum(1 for ep in episodes if ep.fp_class == "transient")
    fp_other = sum(1 for ep in episodes if ep.fp_class == "other_repair")
    fp_no = sum(1 for ep in episodes if ep.fp_class == "no_repair")

    return WellResult(
        target=target,
        series=series,
        base_moment=base_moment,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        rod_break_repairs=list(rod_break_repairs),
        rod_break_repairs_in_coverage=in_coverage,
        other_repairs=list(other_repairs),
        episodes=episodes,
        tp=tp,
        fn=len(missed),
        fp_transient=fp_transient,
        fp_other_repair=fp_other,
        fp_no_repair=fp_no,
        missed_repair_ids=missed,
    )
