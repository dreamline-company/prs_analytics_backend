from datetime import UTC, datetime, timedelta

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import RawBucket
from apps.detectors.rod_breaks.rule import evaluate
from apps.detectors.rod_breaks.services import bucketizer

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _raw(idx: int, mom_min: float, spd: float) -> RawBucket:
    return RawBucket(
        start_ts=T0 + timedelta(hours=2 * idx),
        mom_min=mom_min,
        spd=spd,
        sample_count=60,
    )


def test_median_none_until_min_buckets() -> None:
    raw = [_raw(i, 120.0, 130.0) for i in range(config.MIN_MEDIAN_BUCKETS + 2)]

    series = bucketizer.build_series(raw)

    # Короткое окно в начале ряда -> медиана не считается.
    assert series[0].mom_med24 is None
    # Как только набралось MIN_MEDIAN_BUCKETS корзин -> медиана есть.
    assert series[config.MIN_MEDIAN_BUCKETS - 1].mom_med24 == 120.0
    assert series[config.MIN_MEDIAN_BUCKETS - 1].spd_med24 == 130.0


def test_trailing_window_ignores_gaps() -> None:
    # 12 корзин, затем разрыв 5 дней, затем одна корзина: в её 24ч-окне только
    # она сама -> медиана None (старые корзины по времени не попадают).
    raw = [_raw(i, 120.0, 130.0) for i in range(12)]
    far = RawBucket(T0 + timedelta(days=5), 100.0, 120.0, 60)

    series = bucketizer.build_series([*raw, far])

    assert series[-1].mom_med24 is None


def test_pipeline_rod_break_end_to_end() -> None:
    # Полный чистый конвейер: сырые корзины -> бакетизатор -> правило.
    raw = [_raw(i, 120.0, 130.0) for i in range(20)]
    raw += [_raw(20, 8.0, 130.0), _raw(21, 7.0, 129.0)]

    series = bucketizer.build_series(raw)
    result = evaluate(series)

    assert result.fired is True
    assert result.fired_at == T0 + timedelta(hours=2 * 20)
