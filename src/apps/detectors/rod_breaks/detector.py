"""Сборка детектора R2: телеметрия → корзины → правило → failure_dt.

Единственная точка склейки: use-case и таска зовут только ``run_for_well``.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.rule import RuleResult, evaluate
from apps.detectors.rod_breaks.services import bucketizer, failure_dt
from apps.detectors.rod_breaks.services.telemetry_source import (
    RodBreakTelemetrySource,
)
from apps.telemetry.repositories.sdmo import SdmoStationRepository

# Целевой флот детектора — Danfoss VLT (ЭВН-КУДУ).
TARGET_TYPE_1900 = 6
# Сколько корзин вокруг сработки складывать в улики.
_EVIDENCE_HALF_WINDOW = 6
_SECONDS_PER_HOUR = 3600


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Итог детекции по одной скважине за один прогон."""

    well_id: int
    station_sdmo_id: int | None
    fired: bool
    fired_at: datetime | None
    failure_dt: datetime | None
    lead_time_hours: float | None
    event_class: str | None
    base_moment: float | None
    low_confidence: bool
    evidence: list[dict]


def _as_naive_utc(moment: datetime) -> datetime:
    """Привести дату к наивному UTC — savetime в БД хранится наивным UTC."""
    if moment.tzinfo is None:
        return moment
    return moment.astimezone(UTC).replace(tzinfo=None)


def _build_evidence(
    series: Sequence[Bucket2h],
    rule_result: RuleResult,
) -> list[dict]:
    """Сериализовать корзины вокруг сработки (или хвост ряда) для аудита."""
    if not series:
        return []

    if rule_result.fired_at is not None:
        center = next(
            (i for i, b in enumerate(series) if b.start_ts == rule_result.fired_at),
            len(series) - 1,
        )
        lo = max(0, center - _EVIDENCE_HALF_WINDOW)
        hi = min(len(series), center + _EVIDENCE_HALF_WINDOW)
        window = series[lo:hi]
    else:
        window = series[-_EVIDENCE_HALF_WINDOW * 2 :]

    return [
        {
            "start_ts": bucket.start_ts.isoformat(),
            "mom_min": bucket.mom_min,
            "spd": bucket.spd,
            "mom_med24": bucket.mom_med24,
            "spd_med24": bucket.spd_med24,
        }
        for bucket in window
    ]


class RodBreakDetector:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.source = RodBreakTelemetrySource(session)
        self.station_repo = SdmoStationRepository(session)

    async def run_for_well(
        self,
        well_id: int,
        as_of: datetime,
    ) -> DetectionResult:
        """Прогнать R2 по скважине на дату ``as_of`` (правый край окна 60 дней)."""
        window_end = _as_naive_utc(as_of)
        window_start = window_end - timedelta(days=config.WINDOW_DAYS)

        station_sdmo_id = await self._resolve_station(well_id)
        if station_sdmo_id is None:
            return DetectionResult(
                well_id=well_id,
                station_sdmo_id=None,
                fired=False,
                fired_at=None,
                failure_dt=None,
                lead_time_hours=None,
                event_class=None,
                base_moment=None,
                low_confidence=False,
                evidence=[],
            )

        raw = await self.source.load_raw_buckets(
            station_sdmo_id,
            window_start,
            window_end,
        )
        series = bucketizer.build_series(raw)
        rule_result = evaluate(series)

        base_moment = await self.source.get_base_moment(
            station_sdmo_id,
            window_start,
            window_end,
        )
        recovered_failure_dt = failure_dt.recover_failure_dt(series, base_moment)
        event_class = failure_dt.classify_event(recovered_failure_dt, window_end)

        lead_time_hours = None
        if rule_result.fired_at is not None and recovered_failure_dt is not None:
            delta = recovered_failure_dt - rule_result.fired_at
            lead_time_hours = delta.total_seconds() / _SECONDS_PER_HOUR

        low_confidence = (
            base_moment is not None and base_moment < config.MIN_BASE_MOMENT
        )

        return DetectionResult(
            well_id=well_id,
            station_sdmo_id=station_sdmo_id,
            fired=rule_result.fired,
            fired_at=rule_result.fired_at,
            failure_dt=recovered_failure_dt,
            lead_time_hours=lead_time_hours,
            event_class=event_class,
            base_moment=base_moment,
            low_confidence=low_confidence,
            evidence=_build_evidence(series, rule_result),
        )

    async def _resolve_station(self, well_id: int) -> int | None:
        """Выбрать sdmo_id станции type_1900=6 для скважины (первую по порядку)."""
        stations = await self.station_repo.list_by_well_id(well_id)
        for station in stations:
            if station.type_1900 == TARGET_TYPE_1900:
                return station.sdmo_id
        return None
