"""Данные одной строки ведомости, собранные из БД, — вход текстов и ТОП."""

from dataclasses import dataclass, field
from datetime import datetime

from apps.detectors.services.daily_sheet.config import LOSS_RATIO
from apps.detectors.services.daily_sheet.metrics import ratio
from apps.detectors.services.daily_sheet.selection import (
    Episode,
    IncidentLike,
    severity_key,
)

# Шаг округления вероятности в бланке.
_PROBABILITY_STEP = 5


@dataclass(slots=True)
class RateSnapshot:
    """Дебиты на дату ведомости: последний замер до конца суток и режим."""

    liquid: float | None = None
    oil: float | None = None
    water_cut: float | None = None
    measured_at: datetime | None = None
    # Замер старше TELEMETRY_FRESH_DAYS — в бланке «нет замеров».
    stale: bool = False
    plan_liquid: float | None = None
    plan_oil: float | None = None

    @property
    def has_fresh(self) -> bool:
        return self.measured_at is not None and not self.stale


@dataclass(slots=True)
class RepairInfo:
    start: datetime
    end: datetime | None


@dataclass(slots=True)
class WellContext:
    detector_code: str
    well_id: int
    well_name: str
    # Эпизоды в окне ведомости, главный первым (см. selection.group_by_well).
    episodes: list[Episode]
    # Более ранние эпизоды за REPEAT_LOOKBACK_DAYS — пометка «повторно».
    previous: list[IncidentLike] = field(default_factory=list)
    rates: RateSnapshot = field(default_factory=RateSnapshot)
    repairs: list[RepairInfo] = field(default_factory=list)
    confidence: float = 0.5
    cause: str = ""

    @property
    def primary(self) -> Episode:
        return self.episodes[0]

    @property
    def severity(self) -> str:
        return severity_key(self.detector_code, self.primary.incident.payload)

    @property
    def probability_percent(self) -> int:
        return int(round(self.confidence * 100 / _PROBABILITY_STEP) * _PROBABILITY_STEP)

    @property
    def liquid_ratio(self) -> float | None:
        if not self.rates.has_fresh:
            return None
        return ratio(self.rates.liquid, self.rates.plan_liquid)

    @property
    def losses(self) -> bool:
        return self.liquid_ratio is not None and self.liquid_ratio < LOSS_RATIO
