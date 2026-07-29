from datetime import UTC, datetime, timedelta

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.rule import evaluate, is_flagged

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _bucket(
    idx: int,
    mom_min: float,
    spd: float,
    mom_med24: float | None = 120.0,
    spd_med24: float | None = 130.0,
) -> Bucket2h:
    return Bucket2h(
        start_ts=T0 + timedelta(hours=2 * idx),
        mom_min=mom_min,
        spd=spd,
        mom_med24=mom_med24,
        spd_med24=spd_med24,
    )


def test_vmb0640_rod_break_fires() -> None:
    # Калибр: обрыв 46-й штанги. Обвал момента при сохранённой скорости.
    buckets = [_bucket(i, 120.0, 130.0) for i in range(10)]
    buckets += [
        _bucket(10 + k, 10.0 - k, 130.0 - k) for k in range(config.SUSTAIN_BUCKETS)
    ]

    result = evaluate(buckets)

    assert result.fired is True
    assert result.fired_at == T0 + timedelta(hours=2 * 10)


def test_vmb1286_planned_stop_no_fire() -> None:
    # Калибр: штатная остановка. Момент упал, но и скорость обнулилась.
    buckets = [_bucket(i, 120.0, 130.0) for i in range(10)]
    buckets += [_bucket(10, 5.0, 0.0), _bucket(11, 4.0, 0.0)]

    result = evaluate(buckets)

    assert result.fired is False
    assert result.fired_at is None


def test_single_spike_no_fire() -> None:
    # Одиночный провал среди здоровых корзин — сустейн 2 не набирается.
    buckets = [_bucket(i, 120.0, 130.0) for i in range(10)]
    buckets[5] = _bucket(5, 8.0, 130.0)

    result = evaluate(buckets)

    assert result.fired is False


def test_incomplete_median_not_flagged() -> None:
    # Мало истории (медиана None) — корзина не флагуется даже при нулевом моменте.
    bucket = Bucket2h(T0, 1.0, 130.0, mom_med24=None, spd_med24=None)

    assert is_flagged(bucket) is False


def test_fired_at_is_first_of_series() -> None:
    # Дата сработки = левая граница ПЕРВОЙ корзины устойчивой серии.
    buckets = [_bucket(i, 120.0, 130.0) for i in range(6)]
    buckets += [_bucket(6 + k, 5.0, 130.0) for k in range(config.SUSTAIN_BUCKETS + 1)]

    result = evaluate(buckets)

    assert result.fired_at == T0 + timedelta(hours=2 * 6)
