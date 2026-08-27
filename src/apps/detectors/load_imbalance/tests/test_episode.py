"""Тесты жизненного цикла эпизода R9 (warning -> alarm -> normalized)."""

from datetime import date, timedelta

from apps.detectors.load_imbalance import incident_config
from apps.detectors.load_imbalance.dto.internal.day import (
    BRANCH_ABSOLUTE,
    STATE_ALERT,
    STATE_CLEAN,
    STATE_GREY,
    STATE_UNDETERMINED,
    Baseline,
    DayVerdict,
)
from apps.detectors.load_imbalance.services.episode import (
    ACTION_CONFIRM,
    ACTION_ESCALATE,
    ACTION_NORMALIZE,
    ACTION_OPEN_WARNING,
    evaluate_episode,
)
from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)

D0 = date(2026, 1, 1)
BASELINE = Baseline(n_days=30, k_p90=0.05, p95_median=3700.0)


def _verdict(idx: int, state: str) -> DayVerdict:
    alert = state == STATE_ALERT
    return DayVerdict(
        day=D0 + timedelta(days=idx),
        state=state,
        n_samples=288,
        p5=-900.0 if alert else -90.0,
        p50=1400.0,
        p95=3700.0,
        k=0.24 if alert else 0.03,
        branches=(BRANCH_ABSOLUTE,) if alert else (),
        baseline=BASELINE,
    )


def _kinds(actions: list) -> list[str]:
    return [action.kind for action in actions]


def test_first_alert_day_opens_warning() -> None:
    verdicts = [_verdict(0, STATE_CLEAN), _verdict(1, STATE_ALERT)]

    actions = evaluate_episode(verdicts, cursor_day=None, active_level=None)

    assert _kinds(actions) == [ACTION_OPEN_WARNING]
    assert actions[0].day == D0 + timedelta(days=1)


def test_second_alert_day_escalates() -> None:
    verdicts = [_verdict(i, STATE_ALERT) for i in range(2)]

    actions = evaluate_episode(verdicts, cursor_day=None, active_level=None)

    assert _kinds(actions) == [ACTION_OPEN_WARNING, ACTION_ESCALATE]


def test_alert_day_on_active_warning_escalates_across_runs() -> None:
    # Инцидент открыт прошлым прогоном; сегодня снова сработка.
    actions = evaluate_episode(
        [_verdict(0, STATE_ALERT)],
        cursor_day=None,
        active_level=INCIDENT_LEVEL_WARNING,
    )

    assert _kinds(actions) == [ACTION_ESCALATE]


def test_further_alert_days_only_confirm() -> None:
    actions = evaluate_episode(
        [_verdict(0, STATE_ALERT), _verdict(1, STATE_ALERT)],
        cursor_day=None,
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert _kinds(actions) == [ACTION_CONFIRM]
    assert actions[0].day == D0 + timedelta(days=1)


def test_normalizes_after_sustained_clean_run() -> None:
    verdicts = [
        _verdict(i, STATE_CLEAN) for i in range(incident_config.RECOVER_SUSTAIN_DAYS)
    ]

    actions = evaluate_episode(
        verdicts,
        cursor_day=None,
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert _kinds(actions) == [ACTION_NORMALIZE]


def test_single_clean_day_does_not_close() -> None:
    actions = evaluate_episode(
        [_verdict(0, STATE_CLEAN)],
        cursor_day=None,
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert actions == []


def test_grey_day_resets_recovery() -> None:
    # 0.13 <= K < 0.20: тревоги нет, но и до нормы не вернулось - серия рвётся.
    verdicts = [_verdict(0, STATE_CLEAN), _verdict(1, STATE_CLEAN)]
    verdicts += [_verdict(2, STATE_GREY)]
    verdicts += [_verdict(3, STATE_CLEAN), _verdict(4, STATE_CLEAN)]

    actions = evaluate_episode(
        verdicts,
        cursor_day=None,
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert actions == []


def test_undetermined_day_does_not_reset_recovery() -> None:
    # Пропуск данных - не рецидив: серия восстановления продолжается.
    verdicts = [_verdict(0, STATE_CLEAN), _verdict(1, STATE_CLEAN)]
    verdicts += [_verdict(2, STATE_UNDETERMINED)]
    verdicts += [_verdict(3, STATE_CLEAN), _verdict(4, STATE_CLEAN)]

    actions = evaluate_episode(
        verdicts,
        cursor_day=None,
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert _kinds(actions) == [ACTION_NORMALIZE]


def test_days_before_cursor_only_warm_counters() -> None:
    # Три чистых суток до курсора + одни после = серия замкнулась, но действие
    # выдаётся только по суткам после курсора.
    verdicts = [_verdict(i, STATE_CLEAN) for i in range(4)]

    actions = evaluate_episode(
        verdicts,
        cursor_day=D0 + timedelta(days=2),
        active_level=INCIDENT_LEVEL_ALARM,
    )

    assert _kinds(actions) == [ACTION_NORMALIZE]
    assert actions[0].day == D0 + timedelta(days=3)


def test_reopens_after_normalization_in_same_run() -> None:
    verdicts = [
        _verdict(i, STATE_CLEAN) for i in range(incident_config.RECOVER_SUSTAIN_DAYS)
    ]
    verdicts.append(_verdict(incident_config.RECOVER_SUSTAIN_DAYS, STATE_ALERT))

    actions = evaluate_episode(
        verdicts,
        cursor_day=None,
        active_level=INCIDENT_LEVEL_WARNING,
    )

    assert _kinds(actions) == [ACTION_NORMALIZE, ACTION_OPEN_WARNING]
