"""Отбор эпизодов в ведомость и их состояние «на дату» — чистая логика.

Правило не перезапускается: состояние восстанавливается по меткам эпизода
(открыт до конца суток, нормализован или эскалирован после). Так ведомость за
прошлую дату получается той же, какой была бы утром следующего дня.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Protocol

from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_ACTIVE,
    INCIDENT_STATUS_NORMALIZED,
)
from apps.detectors.services.daily_sheet.config import (
    CATEGORY_HIGH,
    CATEGORY_LOW,
    CATEGORY_LOW_MAX_OIL,
    CATEGORY_MID,
    CATEGORY_MID_MAX_OIL,
    NORMALIZED_TAIL_DAYS,
    R2_RATIO_MARKED,
    R2_RATIO_STRONG,
    R9_K_MARKED,
    R9_K_STRONG,
    SEVERITY_MARKED,
    SEVERITY_MODERATE,
    SEVERITY_STRONG,
)


class IncidentLike(Protocol):
    """Поля эпизода, нужные ведомости (ORM-модель или заглушка в тестах)."""

    id: int
    well_id: int
    entity_id: int | None
    detector_code: str
    level: str
    status: str
    opened_at: datetime
    detected_at: datetime
    last_seen_at: datetime
    escalated_at: datetime | None
    normalized_at: datetime | None
    payload: dict | None


@dataclass(frozen=True, slots=True)
class Episode:
    """Эпизод и его состояние на дату ведомости (а не текущее в БД)."""

    incident: IncidentLike
    status: str
    level: str

    @property
    def is_active(self) -> bool:
        return self.status == INCIDENT_STATUS_ACTIVE


def day_bounds(sheet_date: date) -> tuple[datetime, datetime]:
    """[начало суток, начало следующих) — наивно, как время в БД."""
    start = datetime.combine(sheet_date, time.min)
    return start, start + timedelta(days=1)


def in_scope(
    incident: IncidentLike,
    *,
    day_start: datetime,
    day_end: datetime,
    tail_days: int = NORMALIZED_TAIL_DAYS,
) -> bool:
    """Открыт до конца суток и либо ещё активен, либо закрыт не слишком давно."""
    if incident.opened_at >= day_end:
        return False
    if incident.normalized_at is None:
        return True
    return incident.normalized_at >= day_start - timedelta(days=tail_days)


def state_on_date(incident: IncidentLike, *, day_end: datetime) -> tuple[str, str]:
    """(status, level) эпизода на конец суток ведомости.

    Нормализация после суток — на дату эпизод ещё активен. Уровень alarm без
    ``escalated_at`` означает открытие сразу алармом (R2 по аварийному порогу).
    """
    normalized = incident.normalized_at is not None and incident.normalized_at < day_end
    status = INCIDENT_STATUS_NORMALIZED if normalized else INCIDENT_STATUS_ACTIVE
    escalated = incident.escalated_at is None or incident.escalated_at < day_end
    level = (
        INCIDENT_LEVEL_ALARM
        if incident.level == INCIDENT_LEVEL_ALARM and escalated
        else INCIDENT_LEVEL_WARNING
    )
    return status, level


def episodes_on_date(
    incidents: Sequence[IncidentLike],
    *,
    sheet_date: date,
    tail_days: int = NORMALIZED_TAIL_DAYS,
) -> list[Episode]:
    day_start, day_end = day_bounds(sheet_date)
    result = []
    for incident in incidents:
        if not in_scope(
            incident,
            day_start=day_start,
            day_end=day_end,
            tail_days=tail_days,
        ):
            continue
        status, level = state_on_date(incident, day_end=day_end)
        result.append(Episode(incident=incident, status=status, level=level))
    return result


def group_by_well(episodes: Sequence[Episode]) -> dict[int, list[Episode]]:
    """Эпизоды по скважине, главный первым: активный, затем самый свежий."""
    grouped: dict[int, list[Episode]] = {}
    for episode in episodes:
        grouped.setdefault(episode.incident.well_id, []).append(episode)
    for items in grouped.values():
        items.sort(key=lambda e: (not e.is_active, -e.incident.opened_at.timestamp()))
    return grouped


def severity_key(detector_code: str, payload: dict | None) -> str:
    """Сила отклонения из улик правила; без улик — умеренная.

    R9 растёт с K (чем больше обратный момент, тем сильнее), R2 — с падением
    доли момента от базы (чем меньше, тем сильнее), поэтому знак сравнения
    у правил противоположный.
    """
    payload = payload or {}
    if detector_code == "R9":
        return _band(payload.get("k"), strong=R9_K_STRONG, marked=R9_K_MARKED)
    if detector_code == "R2":
        return _band(
            payload.get("current_ratio"),
            strong=R2_RATIO_STRONG,
            marked=R2_RATIO_MARKED,
            lower_is_worse=True,
        )
    return SEVERITY_MODERATE


def _band(
    value: float | None,
    *,
    strong: float,
    marked: float,
    lower_is_worse: bool = False,
) -> str:
    if value is None:
        return SEVERITY_MODERATE
    if lower_is_worse:
        value, strong, marked = -value, -strong, -marked
    if value >= strong:
        return SEVERITY_STRONG
    return SEVERITY_MARKED if value >= marked else SEVERITY_MODERATE


def category_key(plan_oil: float | None) -> str | None:
    """Категория по техрежиму нефти; без режима категория не определена."""
    if plan_oil is None:
        return None
    if plan_oil < CATEGORY_LOW_MAX_OIL:
        return CATEGORY_LOW
    return CATEGORY_MID if plan_oil < CATEGORY_MID_MAX_OIL else CATEGORY_HIGH
