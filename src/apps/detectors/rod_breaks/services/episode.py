"""State machine эпизода R2: чистая логика переходов, без I/O.

Вход — обогащённые корзины (весь контекст, включая прогрев медианы и сутки до
T0 для базы) и позиция курсора; на срабатывание оцениваются только корзины
после курсора, но счётчики серий (сустейны) учитывают и корзины до него —
серия может пересекать границу.

Стадии (правило заказчика, 19.11.2026; T0 = ``opened_at``, начало провала):

1. Провал момента (серия ниже 60 % за 1 ч или ниже 40 % за 45 мин) открывает
   эпизод всегда уровнем ``warning`` — «Падение нагрузки». В момент открытия
   замораживается база: средний момент за сутки до T0 на рабочих отсчётах.
2. С T0 + 4 ч каждая корзина проверяет последний час: средний момент ниже
   75 % базы (или доказать возврат нечем) — эскалация в ``alarm``, «Возможный
   обрыв штанг».
3. Закрытие: 3 ч подряд медиана момента корзины выше 75 % базы (восстановился);
   начался ремонт; прошло ``STALE_DAYS`` суток. Пока идёт ремонт и до закрытия
   прошлого эпизода новый не открывается.

Выход — упорядоченный список действий для writer'а. За один вызов действий
может быть несколько (догон после простоя: открылся -> эскалировал -> закрылся).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_REPAIR,
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
)
from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.rule import is_flagged

ACTION_OPEN_WARNING = "open_warning"
# Старое открытие сразу аварией: новый код его не порождает, константа — для
# writer'а и истории.
ACTION_OPEN_ALARM = "open_alarm"
ACTION_ESCALATE = "escalate"
ACTION_CONFIRM = "confirm"
ACTION_NORMALIZE = "normalize"

_BUCKET_SPAN = timedelta(hours=config.BUCKET_HOURS)
_ESCALATE_AFTER = timedelta(hours=incident_config.STAGE_ESCALATE_HOURS)
_CHECK_WINDOW = timedelta(hours=incident_config.STAGE_CHECK_WINDOW_HOURS)
_BASE_WINDOW = timedelta(hours=incident_config.BASE_HOURS)
_STALE_AFTER = timedelta(days=incident_config.STALE_DAYS)
_REPAIR_OPEN_MAX = timedelta(days=incident_config.REPAIR_OPEN_MAX_DAYS)


@dataclass(frozen=True, slots=True)
class EpisodeAction:
    """Одно решение state machine.

    Attributes:
        kind: Тип действия (ACTION_*).
        at: Момент события: для open_* — начало серии (opened_at), для
            escalate — правая граница корзины, на которой час проверки не
            показал возврата, для confirm — правая граница последней
            аномальной корзины, для normalize — момент закрытия.
        base: open — замороженная база эпизода (None — не из чего посчитать).
        base_samples: open — сколько рабочих отсчётов дали базу.
        close_reason: normalize — recovered / repair / stale.
        evidence: escalate — цифры проверки часа.
    """

    kind: str
    at: datetime
    base: float | None = None
    base_samples: int = 0
    close_reason: str | None = None
    evidence: dict | None = None


@dataclass(frozen=True, slots=True)
class ActiveEpisode:
    """Активный эпизод из БД: уровень, T0 и его замороженная база."""

    level: str
    opened_at: datetime
    base: float | None
    base_samples: int = 0


@dataclass(frozen=True, slots=True)
class StageCheck:
    """Итог проверки часа: вернулся ли момент к базе.

    ``decided=False`` — в часе мало отсчётов, решать рано.
    """

    recovered: bool
    evidence: dict
    decided: bool = True


@dataclass(frozen=True, slots=True)
class RepairWindow:
    """Ремонт скважины (ABAI, местное время; ``end=None`` — не закрыт)."""

    start: datetime
    end: datetime | None = None


def is_warn_flagged(bucket: Bucket2h) -> bool:
    """Мягкий флаг деградации: как алармный, но с порогом WARN_MOM_DROP_RATIO."""
    if bucket.mom_med24 is None or bucket.spd_med24 is None:
        return False
    return (
        bucket.mom_min < incident_config.WARN_MOM_DROP_RATIO * bucket.mom_med24
        and bucket.spd > config.SPD_KEEP_RATIO * bucket.spd_med24
    )


def is_recovered(bucket: Bucket2h, base_moment: float | None) -> bool:
    """Корзина «здоровой» работы: медиана момента у базы и живая скорость.

    Медиана корзины, а не MIN: у пульсирующих скважин MIN и в норме ниже 75 %
    среднего. Корзина без медианы (тесты, старые данные) — по MIN.
    """
    if base_moment is None or base_moment <= 0:
        return False
    level = bucket.mom_med if bucket.mom_med is not None else bucket.mom_min
    return (
        level > incident_config.RECOVER_RATIO * base_moment
        and bucket.spd > config.FAILURE_SPD_ABS
    )


def frozen_base(
    buckets: Sequence[Bucket2h],
    t0: datetime,
) -> tuple[float | None, int]:
    """База эпизода: средний момент за сутки до T0 на рабочих отсчётах.

    Рабочий отсчёт — скорость выше ``FAILURE_SPD_ABS`` (сводки ``work_*``
    корзин). Возвращает среднее (None — рабочих отсчётов нет) и их число.
    """
    since = t0 - _BASE_WINDOW
    total = 0.0
    count = 0
    for bucket in buckets:
        if since <= bucket.start_ts < t0:
            total += bucket.work_sum
            count += bucket.work_n
    return (total / count if count else None), count


def _round(value: float | None, digits: int = 1) -> float | None:
    return round(value, digits) if value is not None else None


def stage_check(
    buckets: Sequence[Bucket2h],
    *,
    end: datetime,
    base: float | None,
    base_samples: int,
) -> StageCheck:
    """Вернулся ли момент: средний за час до ``end`` ≥ 75 % базы.

    Нет базы или она слабая (< ``BASE_MIN``) — возврат не доказать
    (``decided=True, recovered=False``): эскалация. В часе мало отсчётов
    (перерыв связи) — решать рано (``decided=False``), ждём полный час.
    """
    start = end - _CHECK_WINDOW
    window = [
        b
        for b in buckets
        if b.mom_sum is not None and start <= b.start_ts and b.start_ts < end
    ]
    samples = sum(b.mom_n for b in window)
    filled = sum(1 for b in window if b.mom_n > 0)
    level = sum(b.mom_sum for b in window) / samples if samples else None
    share = level / base if level is not None and base else None
    base_ok = (
        base is not None
        and base >= incident_config.BASE_MIN
        and base_samples >= incident_config.BASE_MIN_SAMPLES
    )
    window_ok = (
        filled >= incident_config.STAGE_WINDOW_MIN_BUCKETS
        and samples >= incident_config.STAGE_WINDOW_MIN_SAMPLES
    )
    recovered = (
        base_ok
        and window_ok
        and share is not None
        and share >= incident_config.STAGE_RECOVER_RATIO
    )
    return StageCheck(
        recovered=recovered,
        decided=(not base_ok) or window_ok,
        evidence={
            "window_from": start.isoformat(),
            "window_to": end.isoformat(),
            "level": _round(level),
            "samples": samples,
            "buckets": filled,
            "base": _round(base),
            "base_samples": base_samples,
            "share_of_base": _round(share, 3),
            "threshold": incident_config.STAGE_RECOVER_RATIO,
            "enough_data": base_ok and window_ok,
        },
    )


def stage_escalation(
    buckets: Sequence[Bucket2h],
    *,
    opened_at: datetime,
    until: datetime,
    base: float | None,
    base_samples: int,
) -> EpisodeAction | None:
    """Когда «Падение нагрузки» стало бы «Возможный обрыв штанг» — для пересчёта
    истории: первая корзина с концом в [T0 + 4 ч, ``until``], на которой час
    проверки не показал возврата момента. None — эпизод остаётся warning."""
    ordered = sorted(buckets, key=lambda b: b.start_ts)
    for bucket in ordered:
        end = bucket.start_ts + _BUCKET_SPAN
        if bucket.start_ts < opened_at or end > until:
            continue
        if end < opened_at + _ESCALATE_AFTER:
            continue
        check = stage_check(ordered, end=end, base=base, base_samples=base_samples)
        if check.decided and not check.recovered:
            return EpisodeAction(ACTION_ESCALATE, end, evidence=check.evidence)
    return None


def _repair_end(repair: RepairWindow) -> datetime:
    return repair.end or repair.start + _REPAIR_OPEN_MAX


def in_repair(moment: datetime, repairs: Sequence[RepairWindow]) -> bool:
    """Шёл ли в ``moment`` ремонт (без конца — не дольше REPAIR_OPEN_MAX_DAYS)."""
    return any(r.start <= moment < _repair_end(r) for r in repairs)


def _forced_close(
    opened_at: datetime,
    repairs: Sequence[RepairWindow],
    before: datetime,
) -> tuple[datetime, str] | None:
    """Закрытие без восстановления раньше ``before``: ремонт или давность.

    Ремонт закрывает эпизод своим началом; если он шёл уже на T0 (сигнал при
    пуске во время ремонта, ремонт загрузили позже) — моментом открытия.
    """
    candidates: list[tuple[datetime, str]] = []
    for repair in repairs:
        if opened_at <= repair.start < before:
            candidates.append((repair.start, CLOSE_REASON_REPAIR))
        elif repair.start < opened_at < _repair_end(repair):
            candidates.append((opened_at, CLOSE_REASON_REPAIR))
    stale_at = opened_at + _STALE_AFTER
    if stale_at < before:
        candidates.append((stale_at, CLOSE_REASON_STALE))
    return min(candidates) if candidates else None


def evaluate_episode(  # noqa: C901, PLR0912, PLR0913, PLR0915 — один проход автомата
    buckets: Sequence[Bucket2h],
    *,
    cursor_ts: datetime | None,
    active: ActiveEpisode | None,
    fallback_base: float | None,
    repairs: Sequence[RepairWindow] = (),
    closed_floor: datetime | None = None,
    now: datetime | None = None,
) -> list[EpisodeAction]:
    """Прогнать корзины через state machine эпизода.

    Args:
        buckets: Обогащённые корзины (контекст + новые), любой порядок.
        cursor_ts: Правый край уже обработанного (конец последней оценённой
            корзины); None — первый прогон, оцениваются все корзины.
        active: Активный эпизод в БД или None.
        fallback_base: Плавающая база прогона — для восстановления, когда у
            эпизода нет своей (до T0 не было рабочих отсчётов).
        repairs: Ремонты скважины (местное время). Начатый после открытия
            ремонт закрывает эпизод; пока ремонт идёт, эпизоды не открываются
            (тревога при пуске в ремонте — артефакт работ).
        closed_floor: Когда закрылся прошлый эпизод правила на скважине: новый
            не открывается раньше (без нахлёста), серия до него не считается.
        now: Настенное время прогона — закрыть ремонтом/давностью, даже если
            новых корзин нет (станция молчит).

    Returns:
        Действия в хронологическом порядке. CONFIRM схлопнут до последнего.
    """
    ordered = sorted(buckets, key=lambda b: b.start_ts)
    repairs = sorted(repairs, key=lambda r: r.start)

    alarm_run = 0
    warn_run = 0
    recover_run = 0
    level = active.level if active else None
    t0 = active.opened_at if active else None
    base = active.base if active else None
    base_samples = active.base_samples if active else 0
    floor = closed_floor
    actions: list[EpisodeAction] = []
    last_confirm: EpisodeAction | None = None

    def close(at: datetime, reason: str) -> None:
        nonlocal level, t0, base, base_samples, last_confirm, floor
        nonlocal alarm_run, warn_run, recover_run
        actions.append(EpisodeAction(ACTION_NORMALIZE, at, close_reason=reason))
        level = t0 = base = None
        base_samples = 0
        alarm_run = warn_run = recover_run = 0
        last_confirm = None
        floor = at

    for bucket in ordered:
        # Без активного эпизода серия не набирается до закрытия прошлого и во
        # время ремонта — ни до курсора, ни после.
        if level is None and (
            (floor is not None and bucket.start_ts < floor)
            or in_repair(bucket.start_ts, repairs)
        ):
            alarm_run = warn_run = recover_run = 0
            continue

        alarm_flag = is_flagged(bucket)
        # Аларм — частный случай деградации: считаем его и в warn-серию.
        warn_flag = alarm_flag or is_warn_flagged(bucket)
        recover_base = base if base is not None else fallback_base
        recover_flag = is_recovered(bucket, recover_base)

        alarm_run = alarm_run + 1 if alarm_flag else 0
        warn_run = warn_run + 1 if warn_flag else 0
        recover_run = recover_run + 1 if recover_flag else 0

        # Корзины до курсора только прогревают счётчики серий.
        if cursor_ts is not None and bucket.start_ts < cursor_ts:
            continue

        bucket_end = bucket.start_ts + _BUCKET_SPAN

        # Ремонт или давность наступили до конца этой корзины — эпизод закрыт
        # ими, корзина ему уже не принадлежит.
        if t0 is not None:
            forced = _forced_close(t0, repairs, bucket_end)
            if forced is not None:
                close(*forced)
                continue

        if level is None:
            sustain = 0
            if alarm_run >= config.SUSTAIN_BUCKETS:
                sustain = config.SUSTAIN_BUCKETS
            elif warn_run >= incident_config.WARN_SUSTAIN_BUCKETS:
                sustain = incident_config.WARN_SUSTAIN_BUCKETS
            if sustain:
                opened_at = bucket_end - sustain * _BUCKET_SPAN
                base, base_samples = frozen_base(ordered, opened_at)
                actions.append(
                    EpisodeAction(
                        ACTION_OPEN_WARNING,
                        opened_at,
                        base=base,
                        base_samples=base_samples,
                    ),
                )
                level = INCIDENT_LEVEL_WARNING
                t0 = opened_at
                recover_run = 0
                last_confirm = None
            continue

        if recover_run >= incident_config.RECOVER_SUSTAIN_BUCKETS:
            close(bucket_end, CLOSE_REASON_RECOVERED)
            continue

        if level == INCIDENT_LEVEL_WARNING and bucket_end >= t0 + _ESCALATE_AFTER:
            check = stage_check(
                ordered,
                end=bucket_end,
                base=base,
                base_samples=base_samples,
            )
            if check.decided and not check.recovered:
                actions.append(
                    EpisodeAction(ACTION_ESCALATE, bucket_end, evidence=check.evidence),
                )
                level = INCIDENT_LEVEL_ALARM
                last_confirm = None
                continue

        if warn_flag:
            last_confirm = EpisodeAction(ACTION_CONFIRM, bucket_end)

    if last_confirm is not None:
        actions.append(last_confirm)

    # Станция молчит (снята на ремонт, нет связи) — новых корзин нет, но
    # ремонт или давность закрывают эпизод по настенным часам.
    if t0 is not None and now is not None:
        forced = _forced_close(t0, repairs, now)
        if forced is not None:
            if last_confirm is not None and last_confirm.at > forced[0]:
                actions.remove(last_confirm)
            close(*forced)

    return actions
