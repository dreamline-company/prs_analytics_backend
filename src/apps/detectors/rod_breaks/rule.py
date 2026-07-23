"""Чистое ядро правила R2 (обрыв штанги). Никакого I/O — только логика.

Физика (spec 5.2): после обрыва вал двигателя крутится без нагрузки — момент
проваливается почти в ноль, а скорость сохраняется. Именно пара «ноль момента +
крутящаяся скорость» отличает аварию от штатной остановки, где обнуляется и то и
другое.

    flag = (mom_min < 0.4 * mom_med24) AND (spd > 0.4 * spd_med24)
    срабатывание = flag в 2 подряд корзинах; дата = первая корзина серии.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h


@dataclass(frozen=True, slots=True)
class RuleResult:
    """Итог применения правила к ряду корзин.

    Attributes:
        fired: Было ли срабатывание (устойчивая серия).
        fired_at: Левая граница первой корзины первой сработавшей серии.
        flagged_buckets: Границы всех корзин, где сработал одиночный flag.
    """

    fired: bool
    fired_at: datetime | None
    flagged_buckets: tuple[datetime, ...]


def is_flagged(bucket: Bucket2h) -> bool:
    """Одиночный флаг R2 по одной корзине (без учёта сустейна).

    Корзины с неполной медианой (мало истории) не флагуются.
    """
    if bucket.mom_med24 is None or bucket.spd_med24 is None:
        return False
    return (
        bucket.mom_min < config.MOM_DROP_RATIO * bucket.mom_med24
        and bucket.spd > config.SPD_KEEP_RATIO * bucket.spd_med24
    )


def evaluate(buckets: Sequence[Bucket2h]) -> RuleResult:
    """Применить R2 к ряду корзин и вернуть первую устойчивую сработку.

    Срабатывание — ``SUSTAIN_BUCKETS`` подряд корзин с ``flag=True`` (фильтр
    против одиночных выбросов). Возвращается самая ранняя такая серия; дата
    сработки — левая граница её первой корзины.
    """
    ordered = sorted(buckets, key=lambda b: b.start_ts)
    flags = [is_flagged(b) for b in ordered]
    flagged_buckets = tuple(
        b.start_ts for b, flag in zip(ordered, flags, strict=True) if flag
    )

    run = 0
    for i, flag in enumerate(flags):
        run = run + 1 if flag else 0
        if run >= config.SUSTAIN_BUCKETS:
            first_of_series = ordered[i - config.SUSTAIN_BUCKETS + 1]
            return RuleResult(
                fired=True,
                fired_at=first_of_series.start_ts,
                flagged_buckets=flagged_buckets,
            )

    return RuleResult(fired=False, fired_at=None, flagged_buckets=flagged_buckets)
