"""State machine эпизода R2: чистая логика переходов, без I/O.

Вход — обогащённые корзины (весь контекст, включая прогрев медианы) и позиция
курсора; на срабатывание оцениваются только корзины после курсора, но счётчики
серий (сустейны) учитывают и корзины до него — серия может пересекать границу.

Выход — упорядоченный список действий для writer'а: открыть warning/alarm,
эскалировать, подтвердить (last_seen), нормализовать. За один вызов действий
может быть несколько (догон после простоя: открылся -> эскалировал -> закрылся).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.rule import is_flagged

ACTION_OPEN_WARNING = "open_warning"
ACTION_OPEN_ALARM = "open_alarm"
ACTION_ESCALATE = "escalate"
ACTION_CONFIRM = "confirm"
ACTION_NORMALIZE = "normalize"

_BUCKET_SPAN = timedelta(hours=config.BUCKET_HOURS)


@dataclass(frozen=True, slots=True)
class EpisodeAction:
    """Одно решение state machine.

    Attributes:
        kind: Тип действия (ACTION_*).
        at: Момент события: для open_* — начало серии (opened_at), для
            confirm — правая граница последней аномальной корзины, для
            normalize — правая граница корзины, замкнувшей восстановление.
    """

    kind: str
    at: datetime


def is_warn_flagged(bucket: Bucket2h) -> bool:
    """Мягкий флаг деградации: как алармный, но с порогом WARN_MOM_DROP_RATIO."""
    if bucket.mom_med24 is None or bucket.spd_med24 is None:
        return False
    return (
        bucket.mom_min < incident_config.WARN_MOM_DROP_RATIO * bucket.mom_med24
        and bucket.spd > config.SPD_KEEP_RATIO * bucket.spd_med24
    )


def is_recovered(bucket: Bucket2h, base_moment: float | None) -> bool:
    """Корзина «здоровой» работы: момент у базы и живая скорость."""
    if base_moment is None or base_moment <= 0:
        return False
    return (
        bucket.mom_min > incident_config.RECOVER_RATIO * base_moment
        and bucket.spd > config.FAILURE_SPD_ABS
    )


def evaluate_episode(
    buckets: Sequence[Bucket2h],
    *,
    cursor_ts: datetime | None,
    active_level: str | None,
    base_moment: float | None,
) -> list[EpisodeAction]:
    """Прогнать корзины через state machine эпизода.

    Args:
        buckets: Обогащённые корзины (контекст + новые), любой порядок.
        cursor_ts: Правый край уже обработанного (конец последней оценённой
            корзины); None — первый прогон, оцениваются все корзины.
        active_level: Уровень активного эпизода в БД (warning/alarm) или None.
        base_moment: База момента скважины — для условия восстановления.

    Returns:
        Действия в хронологическом порядке. CONFIRM схлопнут до последнего.
    """
    ordered = sorted(buckets, key=lambda b: b.start_ts)

    alarm_run = 0
    warn_run = 0
    recover_run = 0
    level = active_level
    actions: list[EpisodeAction] = []
    last_confirm: EpisodeAction | None = None

    for bucket in ordered:
        alarm_flag = is_flagged(bucket)
        # Аларм — частный случай деградации: считаем его и в warn-серию.
        warn_flag = alarm_flag or is_warn_flagged(bucket)
        recover_flag = is_recovered(bucket, base_moment)

        alarm_run = alarm_run + 1 if alarm_flag else 0
        warn_run = warn_run + 1 if warn_flag else 0
        recover_run = recover_run + 1 if recover_flag else 0

        # Корзины до курсора только прогревают счётчики серий.
        if cursor_ts is not None and bucket.start_ts < cursor_ts:
            continue

        bucket_end = bucket.start_ts + _BUCKET_SPAN

        if level is None:
            if alarm_run >= config.SUSTAIN_BUCKETS:
                opened_at = bucket_end - config.SUSTAIN_BUCKETS * _BUCKET_SPAN
                actions.append(EpisodeAction(ACTION_OPEN_ALARM, opened_at))
                level = "alarm"
                last_confirm = None
            elif warn_run >= incident_config.WARN_SUSTAIN_BUCKETS:
                opened_at = (
                    bucket_end - incident_config.WARN_SUSTAIN_BUCKETS * _BUCKET_SPAN
                )
                actions.append(EpisodeAction(ACTION_OPEN_WARNING, opened_at))
                level = "warning"
                last_confirm = None
            continue

        if level == "warning" and alarm_run >= config.SUSTAIN_BUCKETS:
            actions.append(EpisodeAction(ACTION_ESCALATE, bucket_end))
            level = "alarm"
            last_confirm = None
            continue

        if recover_run >= incident_config.RECOVER_SUSTAIN_BUCKETS:
            actions.append(EpisodeAction(ACTION_NORMALIZE, bucket_end))
            level = None
            alarm_run = 0
            warn_run = 0
            last_confirm = None
            continue

        if warn_flag:
            last_confirm = EpisodeAction(ACTION_CONFIRM, bucket_end)

    if last_confirm is not None:
        actions.append(last_confirm)

    return actions
