"""Тесты state machine эпизода (warning -> alarm -> normalized)."""

from datetime import datetime, timedelta

from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.services.episode import (
    ACTION_CONFIRM,
    ACTION_ESCALATE,
    ACTION_NORMALIZE,
    ACTION_OPEN_ALARM,
    ACTION_OPEN_WARNING,
    evaluate_episode,
)

# naive UTC — как savetime в БД
T0 = datetime(2026, 1, 10)  # noqa: DTZ001
SPAN = timedelta(hours=config.BUCKET_HOURS)

BASE = 500.0
MED = 500.0
SPD = 190.0

# Уровни момента относительно медианы 500: здоровый / деградация / обвал.
MOM_OK = 450.0  # 0.9 — выше RECOVER_RATIO(0.75)*базы
MOM_WARN = 275.0  # 0.55 — между 0.4 и 0.6
MOM_ALARM = 15.0  # 0.03 — глубоко под 0.4


def _bucket(idx: int, mom: float, spd: float = SPD) -> Bucket2h:
    return Bucket2h(
        start_ts=T0 + idx * SPAN,
        mom_min=mom,
        spd=spd,
        mom_med24=MED,
        spd_med24=SPD,
    )


def _series(*segments: tuple[int, float]) -> list[Bucket2h]:
    """Собрать ряд из сегментов (количество корзин, уровень момента)."""
    buckets: list[Bucket2h] = []
    for count, mom in segments:
        start = len(buckets)
        buckets += [_bucket(start + i, mom) for i in range(count)]
    return buckets


def test_warning_opens_after_sustain() -> None:
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((10, MOM_OK), (warn_n, MOM_WARN))

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    assert [a.kind for a in actions] == [ACTION_OPEN_WARNING]
    # opened_at — начало warn-серии.
    assert actions[0].at == T0 + 10 * SPAN


def test_warning_below_sustain_does_not_open() -> None:
    buckets = _series(
        (10, MOM_OK),
        (incident_config.WARN_SUSTAIN_BUCKETS - 1, MOM_WARN),
    )

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    assert actions == []


def test_full_lifecycle_warning_alarm_normalize() -> None:
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    alarm_n = config.SUSTAIN_BUCKETS
    recover_n = incident_config.RECOVER_SUSTAIN_BUCKETS
    buckets = _series(
        (10, MOM_OK),
        (warn_n, MOM_WARN),
        (alarm_n, MOM_ALARM),
        (recover_n, MOM_OK),
    )

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    assert [a.kind for a in actions] == [
        ACTION_OPEN_WARNING,
        ACTION_ESCALATE,
        ACTION_NORMALIZE,
    ]


def test_instant_alarm_without_warning_phase() -> None:
    alarm_n = config.SUSTAIN_BUCKETS
    buckets = _series((10, MOM_OK), (alarm_n, MOM_ALARM))

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    # Обвал набирает warn-сустейн (4) не раньше алармного (3)? Наоборот:
    # аларм-серия из 3 корзин короче warn-серии из 4 — открывается сразу alarm.
    assert [a.kind for a in actions] == [ACTION_OPEN_ALARM]
    assert actions[0].at == T0 + 10 * SPAN


def test_planned_stop_no_actions() -> None:
    # Штатная остановка: момент и скорость падают вместе — не сигнал R2.
    buckets = [_bucket(i, MOM_OK) for i in range(10)]
    buckets += [_bucket(10 + i, 5.0, spd=0.0) for i in range(8)]

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    assert actions == []


def test_active_warning_confirmed_and_normalized() -> None:
    recover_n = incident_config.RECOVER_SUSTAIN_BUCKETS
    buckets = _series((2, MOM_WARN), (recover_n, MOM_OK))

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level="warning",
        base_moment=BASE,
    )

    # Подтверждения до нормализации схлопываются: остаётся только закрытие.
    assert [a.kind for a in actions] == [ACTION_NORMALIZE]


def test_recovery_below_sustain_keeps_active() -> None:
    buckets = _series(
        (2, MOM_WARN),
        (incident_config.RECOVER_SUSTAIN_BUCKETS - 1, MOM_OK),
    )

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level="alarm",
        base_moment=BASE,
    )

    # Восстановление не добрало сустейн — эпизод жив, последний CONFIRM
    # указывает на последнюю аномальную корзину.
    assert [a.kind for a in actions] == [ACTION_CONFIRM]
    assert actions[0].at == T0 + 2 * SPAN


def test_series_across_cursor_boundary_opens() -> None:
    """Серия началась до курсора, сустейн добрался после — эпизод открывается."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((10, MOM_OK), (warn_n, MOM_WARN))
    # Курсор — перед последней корзиной серии: (warn_n - 1) корзин уже видели.
    cursor_ts = buckets[10 + warn_n - 1].start_ts

    actions = evaluate_episode(
        buckets,
        cursor_ts=cursor_ts,
        active_level=None,
        base_moment=BASE,
    )

    assert [a.kind for a in actions] == [ACTION_OPEN_WARNING]
    assert actions[0].at == T0 + 10 * SPAN


def test_buckets_before_cursor_never_reopen() -> None:
    """Старая (уже обработанная) серия не даёт действий при повторном чтении."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((10, MOM_OK), (warn_n, MOM_WARN), (2, MOM_OK))
    cursor_ts = buckets[-1].start_ts + SPAN  # всё уже оценено

    actions = evaluate_episode(
        buckets,
        cursor_ts=cursor_ts,
        active_level=None,
        base_moment=BASE,
    )

    assert actions == []


def test_catchup_full_history_in_one_pass() -> None:
    """Догон после простоя: открыть, эскалировать и закрыть за один вызов."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    alarm_n = config.SUSTAIN_BUCKETS
    recover_n = incident_config.RECOVER_SUSTAIN_BUCKETS
    buckets = _series(
        (6, MOM_OK),
        (warn_n, MOM_WARN),
        (alarm_n, MOM_ALARM),
        (recover_n, MOM_OK),
        (warn_n, MOM_WARN),
    )

    actions = evaluate_episode(
        buckets,
        cursor_ts=None,
        active_level=None,
        base_moment=BASE,
    )

    # Второй эпизод открывается после нормализации первого.
    assert [a.kind for a in actions] == [
        ACTION_OPEN_WARNING,
        ACTION_ESCALATE,
        ACTION_NORMALIZE,
        ACTION_OPEN_WARNING,
    ]
