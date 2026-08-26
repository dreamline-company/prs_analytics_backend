"""Восстановление фактической даты отказа и классификация события (spec 5).

failure_dt — последний момент, когда насос реально работал. Между ним и
журнальной датой ремонта бывает от часов до 5 недель, поэтому метрики R2 надо
считать от failure_dt, а не от приезда бригады.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h

# Класс события отказа (восстанавливается из телеметрии, см. spec раздел 5).
EVENT_CLASS_ACTIONABLE = "actionable"
EVENT_CLASS_FAILED_LONG_BEFORE = "failed_long_before_repair"
EVENT_CLASS_ALREADY_STOPPED = "already_stopped"


def recover_failure_dt(
    buckets: Sequence[Bucket2h],
    base_moment: float | None,
) -> datetime | None:
    """Правая граница последней корзины, где насос ещё реально работал.

    «Работал» = минимум момента в корзине выше ``FAILURE_MOM_RATIO`` от базы И
    скорость выше ``FAILURE_SPD_ABS``. ``None`` — если база неизвестна или в окне
    нет ни одной рабочей корзины (скважина стояла всё окно).
    """
    if base_moment is None or base_moment <= 0:
        return None

    mom_threshold = config.FAILURE_MOM_RATIO * base_moment
    bucket_span = timedelta(hours=config.BUCKET_HOURS)
    last_working: datetime | None = None

    for bucket in sorted(buckets, key=lambda b: b.start_ts):
        if bucket.mom_min > mom_threshold and bucket.spd > config.FAILURE_SPD_ABS:
            last_working = bucket.start_ts + bucket_span

    return last_working


def classify_event(
    failure_dt: datetime | None,
    window_end: datetime,
) -> str:
    """Отнести событие к категории относительно правого края окна.

    - ``already_stopped`` — рабочих корзин в окне нет вовсе;
    - ``actionable`` — отказ свежий (gap до конца окна не больше 7 дней);
    - ``failed_long_before_repair`` — отказ случился более 7 дней назад.
    """
    if failure_dt is None:
        return EVENT_CLASS_ALREADY_STOPPED

    gap = window_end - failure_dt
    if gap <= timedelta(days=config.ACTIONABLE_GAP_DAYS):
        return EVENT_CLASS_ACTIONABLE

    return EVENT_CLASS_FAILED_LONG_BEFORE
