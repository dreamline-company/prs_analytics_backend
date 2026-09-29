"""Тесты жизненного цикла эпизода R10: когда держать, когда и почему закрывать."""

from datetime import date, timedelta

from apps.detectors.cits_events import config, incident_config
from apps.detectors.cits_events.rule import (
    EVENT_DROP,
    EVENT_STALE,
    EVENT_ZERO,
    KLASS_DROP,
    KLASS_ZERO_PERIODIC,
    SERVICE_CHRONIC_LOW,
    Event,
    ServiceItem,
    WellVerdict,
)
from apps.detectors.cits_events.services.episode import (
    ACTION_CLOSE,
    ACTION_KEEP,
    ACTION_NONE,
    ACTION_UPSERT,
    decide,
)
from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)

FIX = date(2026, 9, 18)
YESTERDAY = FIX - timedelta(days=1)


def _event(event: int, klass: str) -> Event:
    return Event(event=event, klass=klass, last_day=FIX, age_d=0, series_from=FIX)


def test_drop_opens_warning_and_new_zeros_alarm() -> None:
    drop = WellVerdict(event=_event(EVENT_DROP, KLASS_DROP), tail_bad=True)
    zeros = WellVerdict(
        event=_event(EVENT_ZERO, "нулевые замеры, СУ работает"),
        tail_bad=True,
    )

    assert decide(drop, fix=FIX, active_last_seen=None).level == (
        INCIDENT_LEVEL_WARNING
    )
    decision = decide(zeros, fix=FIX, active_last_seen=YESTERDAY)
    assert decision.action == ACTION_UPSERT
    assert decision.level == INCIDENT_LEVEL_ALARM


def test_routine_and_periodic_zeros_stay_warning() -> None:
    for klass in (
        "замеры неустойчивые (нули чередуются с подачей), СУ работает",
        KLASS_ZERO_PERIODIC,
    ):
        verdict = WellVerdict(event=_event(EVENT_ZERO, klass), tail_bad=True)
        assert decide(verdict, fix=FIX, active_last_seen=None).level == (
            INCIDENT_LEVEL_WARNING
        )


def test_nothing_to_do_without_event_and_episode() -> None:
    decision = decide(WellVerdict(tail_bad=False), fix=FIX, active_last_seen=None)

    assert decision.action == ACTION_NONE


def test_close_reasons() -> None:
    cases = {
        incident_config.CLOSE_REASON_IDLE: WellVerdict(idle=True),
        CLOSE_REASON_STALE: WellVerdict(event=_event(EVENT_STALE, "замер устарел")),
        incident_config.CLOSE_REASON_CHRONIC: WellVerdict(
            service=(ServiceItem(SERVICE_CHRONIC_LOW, "хроника", ""),),
            tail_bad=True,
        ),
        CLOSE_REASON_RECOVERED: WellVerdict(tail_bad=False),
    }
    for reason, verdict in cases.items():
        decision = decide(verdict, fix=FIX, active_last_seen=YESTERDAY)
        assert decision.action == ACTION_CLOSE
        assert decision.close_reason == reason


def test_still_bad_measurement_keeps_episode_open() -> None:
    # Серия прервалась (одиночный ноль после отклонения), дебит не вернулся.
    decision = decide(
        WellVerdict(tail_bad=True),
        fix=FIX,
        active_last_seen=YESTERDAY,
    )

    assert decision.action == ACTION_KEEP


def test_unconfirmed_episode_is_closed_after_fresh_window() -> None:
    long_ago = FIX - timedelta(days=config.FRESH_D + 1)

    bad = decide(WellVerdict(tail_bad=True), fix=FIX, active_last_seen=long_ago)
    unknown = decide(WellVerdict(tail_bad=None), fix=FIX, active_last_seen=long_ago)

    assert bad.close_reason == incident_config.CLOSE_REASON_CHRONIC
    assert unknown.close_reason == CLOSE_REASON_STALE
