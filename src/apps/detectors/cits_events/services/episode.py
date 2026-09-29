"""Что делать с эпизодом R10 по состоянию скважины на дату фиксации.

Правило суточное и без памяти: на каждую дату оно заново говорит, есть ли
событие. Эпизоду нужна память, иначе он рвётся на каждом нестрогом дне:
серия отклонения прерывается одиночным нулём, нули — одиночным ненулевым
замером, хотя дебит так и не вернулся. Поэтому эпизод закрывается не по
отсутствию события, а по явной причине:

- событие 1/2 есть            -> подтвердить (warning / alarm);
- скважина в простое ABAI     -> закрыть ``idle``: технологи отреагировали;
- замер устарел               -> закрыть ``stale``: подтвердить нечем;
- серия ушла в хронику        -> закрыть ``chronic``;
- последний замер всё ещё плохой (ноль или за порогом) или не оценить —
  держать открытым, но не дольше FRESH_D без подтверждения;
- последний замер в норме     -> закрыть ``recovered``.
"""

from dataclasses import dataclass
from datetime import date

from apps.detectors.cits_events import config, incident_config
from apps.detectors.cits_events.rule import (
    EVENT_ZERO,
    KLASS_ZERO_PERIODIC,
    KLASS_ZERO_ROUTINE,
    Event,
    WellVerdict,
)
from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)

ACTION_NONE = "none"
ACTION_UPSERT = "upsert"
ACTION_KEEP = "keep"
ACTION_CLOSE = "close"


@dataclass(frozen=True, slots=True)
class EpisodeDecision:
    action: str
    level: str | None = None
    close_reason: str | None = None


def level_for(event: Event) -> str:
    """Новая серия нулей — alarm; отклонение и «привычные» нули — warning."""
    if event.event != EVENT_ZERO:
        return INCIDENT_LEVEL_WARNING
    if event.klass == KLASS_ZERO_PERIODIC or event.klass.startswith(
        KLASS_ZERO_ROUTINE,
    ):
        return INCIDENT_LEVEL_WARNING
    return INCIDENT_LEVEL_ALARM


def decide(  # noqa: PLR0911
    verdict: WellVerdict,
    *,
    fix: date,
    active_last_seen: date | None,
) -> EpisodeDecision:
    """Решение по скважине; ``active_last_seen`` — последние сутки, где
    активный эпизод подтверждался (None — активного эпизода нет)."""
    event = verdict.incident_event
    if event is not None:
        return EpisodeDecision(ACTION_UPSERT, level=level_for(event))
    if active_last_seen is None:
        return EpisodeDecision(ACTION_NONE)

    if verdict.idle:
        return _close(incident_config.CLOSE_REASON_IDLE)
    if verdict.stale:
        return _close(CLOSE_REASON_STALE)
    if verdict.chronic:
        return _close(incident_config.CLOSE_REASON_CHRONIC)
    if verdict.tail_bad is False:
        return _close(CLOSE_REASON_RECOVERED)

    if (fix - active_last_seen).days > config.FRESH_D:
        reason = (
            incident_config.CLOSE_REASON_CHRONIC
            if verdict.tail_bad
            else CLOSE_REASON_STALE
        )
        return _close(reason)
    return EpisodeDecision(ACTION_KEEP)


def _close(reason: str) -> EpisodeDecision:
    return EpisodeDecision(ACTION_CLOSE, close_reason=reason)
