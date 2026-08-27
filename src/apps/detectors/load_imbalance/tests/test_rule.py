"""Тесты правила R9: перцентили, три ветви, каузальный фон."""

from datetime import date, timedelta

from apps.detectors.load_imbalance import config
from apps.detectors.load_imbalance.dto.internal.day import (
    BRANCH_ABSOLUTE,
    BRANCH_LOAD_LOSS,
    BRANCH_RELATIVE,
    STATE_ALERT,
    STATE_CLEAN,
    STATE_GREY,
    STATE_UNDETERMINED,
    Baseline,
    DayAggregate,
)
from apps.detectors.load_imbalance.rule import (
    build_baseline,
    evaluate_day,
    evaluate_series,
    quantile,
)

D0 = date(2026, 1, 1)


def _day(idx: int, p5: float, p95: float, n: int = 288) -> DayAggregate:
    return DayAggregate(
        day=D0 + timedelta(days=idx),
        n_samples=n,
        p5=p5,
        p50=(p5 + p95) / 2,
        p95=p95,
    )


def _baseline(k_p90: float = 0.05, p95_median: float | None = 3700.0) -> Baseline:
    return Baseline(n_days=30, k_p90=k_p90, p95_median=p95_median)


def test_quantile_interpolates_like_scripts() -> None:
    values = [0.0, 1.0, 2.0, 3.0, 4.0]
    assert quantile(values, 0.0) == 0.0
    assert quantile(values, 0.5) == 2.0
    assert quantile(values, 1.0) == 4.0
    assert quantile(values, 0.9) == 3.6


def test_healthy_well_is_clean() -> None:
    # Калибр из графика: P5=-91, P95=3688 -> K = 0.025, норма 0.02-0.08.
    verdict = evaluate_day(_day(0, -91.0, 3688.0), _baseline())

    assert verdict.state == STATE_CLEAN
    assert verdict.k is not None
    assert round(verdict.k, 3) == 0.025


def test_valve_leak_fires_absolute_branch() -> None:
    # Калибр из графика: P5=-896, P95=1926 -> K = 0.465.
    verdict = evaluate_day(_day(0, -896.0, 1926.0), _baseline())

    assert verdict.state == STATE_ALERT
    assert BRANCH_ABSOLUTE in verdict.branches


def test_absolute_branch_muted_on_chronically_unbalanced_well() -> None:
    # Свой фоновый P90 выше BASE_P90_LIMIT: для такой скважины 0.2 - обычная
    # жизнь, абсолютная ветвь глушится. Относительная тоже молчит: 0.22 не
    # дотягивает до 1.5 x 0.20.
    verdict = evaluate_day(_day(0, -800.0, 3700.0), _baseline(k_p90=0.20))

    assert verdict.branches == ()
    assert verdict.state == STATE_GREY


def test_relative_branch_catches_quiet_well() -> None:
    # Тихая скважина: фон 0.04, доросло до 0.15 - до 0.2 далеко, но это x3.75.
    verdict = evaluate_day(_day(0, -555.0, 3700.0), _baseline(k_p90=0.04))

    assert verdict.state == STATE_ALERT
    assert verdict.branches == (BRANCH_RELATIVE,)


def test_load_loss_branch_fires_when_k_degenerates() -> None:
    # Нагрузки нет вовсе: P95 <= 0, K не считается - ловит только ветвь потери.
    verdict = evaluate_day(_day(0, -30.0, 0.0), _baseline(p95_median=3700.0))

    assert verdict.k is None
    assert verdict.state == STATE_ALERT
    assert verdict.branches == (BRANCH_LOAD_LOSS,)


def test_short_day_is_undetermined_not_clean() -> None:
    # Мало снимков - вердикта нет. Считать такие сутки здоровыми нельзя:
    # по ним закрылся бы инцидент.
    verdict = evaluate_day(_day(0, -91.0, 3688.0, n=42), _baseline())

    assert verdict.state == STATE_UNDETERMINED


def test_thin_baseline_blocks_evaluation() -> None:
    history = [_day(i, -100.0, 3700.0) for i in range(config.MIN_BASE_DAYS - 1)]

    assert build_baseline(history) is None
    assert evaluate_day(_day(20, -896.0, 1926.0), None).state == STATE_UNDETERMINED


def test_fired_days_excluded_from_future_baseline() -> None:
    # Медленная деградация: 15 здоровых суток, дальше K держится высоко.
    # Если бы отработавшие сутки попадали в фон, порог уполз бы за деградацией
    # и правило заглушило бы само себя.
    days = [_day(i, -100.0, 3700.0) for i in range(15)]
    days += [_day(15 + i, -900.0, 3700.0) for i in range(10)]

    verdicts = evaluate_series(days)
    tail = [verdict for verdict in verdicts if verdict.day >= D0 + timedelta(days=15)]

    assert all(verdict.state == STATE_ALERT for verdict in tail)


def test_early_days_have_no_baseline() -> None:
    days = [_day(i, -100.0, 3700.0) for i in range(config.MIN_BASE_DAYS + 2)]

    verdicts = evaluate_series(days)

    assert verdicts[0].state == STATE_UNDETERMINED
    assert verdicts[-1].state == STATE_CLEAN
