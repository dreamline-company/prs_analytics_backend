import statistics
from collections.abc import Sequence
from datetime import timedelta

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h, RawBucket


def build_series(raw: Sequence[RawBucket]) -> list[Bucket2h]:
    """Обогатить сырые корзины скользящей медианой момента/скорости за 24 часа.

    Медиана считается по трейлинг-окну ``(start_ts - 24ч, start_ts]`` (12 корзин,
    включая текущую). Окно — по времени, а не по индексу: пропуски корзин (нет
    данных) не «сдвигают» его. Если корзин в окне меньше ``MIN_MEDIAN_BUCKETS`` —
    медианы остаются ``None``, и правило такие корзины игнорирует.

    Args:
        raw: Сырые корзины (в любом порядке).

    Returns:
        Корзины ``Bucket2h`` в хронологическом порядке.
    """
    ordered = sorted(raw, key=lambda b: b.start_ts)
    window = timedelta(hours=config.MEDIAN_WINDOW_HOURS)
    result: list[Bucket2h] = []

    left = 0
    for right, bucket in enumerate(ordered):
        lower_bound = bucket.start_ts - window
        # Сдвигаем левую границу, пока корзины выпадают за пределы 24 часов.
        while ordered[left].start_ts <= lower_bound:
            left += 1
        window_slice = ordered[left : right + 1]

        if len(window_slice) >= config.MIN_MEDIAN_BUCKETS:
            mom_med24 = statistics.median(b.mom_min for b in window_slice)
            spd_med24 = statistics.median(b.spd for b in window_slice)
        else:
            mom_med24 = None
            spd_med24 = None

        result.append(
            Bucket2h(
                start_ts=bucket.start_ts,
                mom_min=bucket.mom_min,
                spd=bucket.spd,
                mom_med24=mom_med24,
                spd_med24=spd_med24,
            ),
        )

    return result
