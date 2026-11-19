"""Тесты state machine эпизода R2.

«Падение нагрузки» -> 4 ч без возврата момента -> «Возможный обрыв штанг».
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

from apps.detectors.models.incident import (
    CLOSE_REASON_RECOVERED,
    CLOSE_REASON_REPAIR,
    CLOSE_REASON_STALE,
)
from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.services.episode import (
    ACTION_CONFIRM,
    ACTION_ESCALATE,
    ACTION_NORMALIZE,
    ACTION_OPEN_WARNING,
    ActiveEpisode,
    EpisodeAction,
    RepairWindow,
    evaluate_episode,
    frozen_base,
    is_recovered,
    stage_escalation,
)

# naive местное время — как savetime в БД
T0 = datetime(2026, 1, 10)  # noqa: DTZ001
SPAN = timedelta(hours=config.BUCKET_HOURS)
PER_HOUR = int(1 / config.BUCKET_HOURS)  # корзин в часе
ESCALATE_N = incident_config.STAGE_ESCALATE_HOURS * PER_HOUR  # 16 корзин = 4 ч
SAMPLES = 8  # отсчётов в корзине (на проде обычно ~3: раз в 5 мин)

MED = 500.0
SPD = 190.0

# Уровни момента относительно медианы 500: здоровый / деградация / обвал.
MOM_OK = 450.0  # 0.9 — выше RECOVER_RATIO(0.75) базы
MOM_WARN = 275.0  # 0.55 — между 0.4 и 0.6
MOM_ALARM = 15.0  # 0.03 — глубоко под 0.4

BASE = MOM_OK  # база эпизода = средний момент суток до T0
OK_BEFORE = 10  # здоровых корзин перед провалом во всех рядах


def _bucket(
    idx: int,
    mom: float,
    spd: float = SPD,
    *,
    mom_min: float | None = None,
) -> Bucket2h:
    working = spd > config.FAILURE_SPD_ABS
    return Bucket2h(
        start_ts=T0 + idx * SPAN,
        mom_min=mom if mom_min is None else mom_min,
        spd=spd,
        mom_med24=MED,
        spd_med24=SPD,
        mom_med=mom,
        mom_sum=mom * SAMPLES,
        mom_n=SAMPLES,
        work_sum=mom * SAMPLES if working else 0.0,
        work_n=SAMPLES if working else 0,
    )


def _series(*segments: tuple[int, float]) -> list[Bucket2h]:
    """Собрать ряд из сегментов (количество корзин, уровень момента)."""
    buckets: list[Bucket2h] = []
    for count, mom in segments:
        start = len(buckets)
        buckets += [_bucket(start + i, mom) for i in range(count)]
    return buckets


def _run(  # noqa: PLR0913
    buckets: list[Bucket2h],
    *,
    active: ActiveEpisode | None = None,
    cursor_ts: datetime | None = None,
    fallback: float | None = BASE,
    repairs: Sequence[RepairWindow] = (),
    now: datetime | None = None,
    floor: datetime | None = None,
) -> list[EpisodeAction]:
    return evaluate_episode(
        buckets,
        cursor_ts=cursor_ts,
        active=active,
        fallback_base=fallback,
        repairs=repairs,
        closed_floor=floor,
        now=now,
    )


def _kinds(actions: list[EpisodeAction]) -> list[str]:
    return [a.kind for a in actions]


def _active(
    level: str = "warning",
    *,
    opened: int = 0,
    base: float | None = BASE,
) -> ActiveEpisode:
    return ActiveEpisode(
        level=level,
        opened_at=T0 + opened * SPAN,
        base=base,
        base_samples=OK_BEFORE * SAMPLES,
    )


# --- Открытие ---------------------------------------------------------------


def test_warning_opens_after_sustain_with_frozen_base() -> None:
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((OK_BEFORE, MOM_OK), (warn_n, MOM_WARN))

    actions = _run(buckets)

    assert _kinds(actions) == [ACTION_OPEN_WARNING]
    # opened_at — начало warn-серии; база — сутки до него.
    assert actions[0].at == T0 + OK_BEFORE * SPAN
    assert actions[0].base == BASE
    assert actions[0].base_samples == OK_BEFORE * SAMPLES


def test_warning_below_sustain_does_not_open() -> None:
    buckets = _series(
        (OK_BEFORE, MOM_OK),
        (incident_config.WARN_SUSTAIN_BUCKETS - 1, MOM_WARN),
    )

    assert _run(buckets) == []


def test_deep_drop_opens_as_warning_not_alarm() -> None:
    """Обвал ниже 40 % открывает эпизод, но первые 4 ч — «Падение нагрузки»."""
    alarm_n = config.SUSTAIN_BUCKETS
    buckets = _series((OK_BEFORE, MOM_OK), (alarm_n, MOM_ALARM))

    actions = _run(buckets)

    assert _kinds(actions) == [ACTION_OPEN_WARNING]
    assert actions[0].at == T0 + OK_BEFORE * SPAN


def test_planned_stop_no_actions() -> None:
    # Штатная остановка: момент и скорость падают вместе — не сигнал R2.
    buckets = [_bucket(i, MOM_OK) for i in range(OK_BEFORE)]
    buckets += [_bucket(OK_BEFORE + i, 5.0, spd=0.0) for i in range(8)]

    assert _run(buckets) == []


# --- Эскалация через 4 часа ---------------------------------------------------


def test_escalates_at_four_hours_when_moment_stays_low() -> None:
    buckets = _series((OK_BEFORE, MOM_OK), (ESCALATE_N + 2, MOM_ALARM))

    actions = _run(buckets)

    assert _kinds(actions) == [ACTION_OPEN_WARNING, ACTION_ESCALATE, ACTION_CONFIRM]
    t0 = actions[0].at
    assert actions[1].at == t0 + timedelta(hours=incident_config.STAGE_ESCALATE_HOURS)
    evidence = actions[1].evidence
    assert evidence["share_of_base"] < incident_config.STAGE_RECOVER_RATIO
    assert evidence["samples"] == PER_HOUR * SAMPLES


def test_no_escalation_before_four_hours() -> None:
    buckets = _series((OK_BEFORE, MOM_OK), (ESCALATE_N - 1, MOM_ALARM))

    assert _kinds(_run(buckets)) == [ACTION_OPEN_WARNING, ACTION_CONFIRM]


def test_moment_back_by_fourth_hour_keeps_load_drop() -> None:
    """Момент вернулся с 1,5 ч: к 4-му часу он у базы — эскалации нет, а через
    3 ч восстановления эпизод закрывается как «Падение нагрузки»."""
    low = 6
    buckets = _series(
        (OK_BEFORE, MOM_OK),
        (low, MOM_ALARM),
        (incident_config.RECOVER_SUSTAIN_BUCKETS, MOM_OK),
    )

    actions = _run(buckets)

    assert _kinds(actions) == [ACTION_OPEN_WARNING, ACTION_NORMALIZE]
    assert actions[1].close_reason == CLOSE_REASON_RECOVERED
    assert actions[1].at > actions[0].at + timedelta(hours=4)


def test_escalates_later_when_moment_drops_again() -> None:
    """К 4-му часу момент вернулся, но снова упал до закрытия — эскалация позже."""
    low = 6
    back = ESCALATE_N - low  # до ровно T0 + 4 ч
    buckets = _series(
        (OK_BEFORE, MOM_OK),
        (low, MOM_ALARM),
        (back, MOM_OK),
        (PER_HOUR, MOM_ALARM),
    )

    actions = _run(buckets)

    assert _kinds(actions)[:2] == [ACTION_OPEN_WARNING, ACTION_ESCALATE]
    assert actions[1].at > actions[0].at + timedelta(hours=4)


def test_weak_base_escalates_without_proof_of_recovery() -> None:
    """База < BASE_MIN — по доле от неё стадию не решаем: эскалация."""
    weak = 40.0

    def bucket(idx: int, mom: float) -> Bucket2h:
        return Bucket2h(
            start_ts=T0 + idx * SPAN,
            mom_min=mom,
            spd=SPD,
            mom_med24=weak,
            spd_med24=SPD,
            mom_med=mom,
            mom_sum=mom * SAMPLES,
            mom_n=SAMPLES,
            work_sum=mom * SAMPLES,
            work_n=SAMPLES,
        )

    moments = [weak] * OK_BEFORE + [4.0] * 3 + [28.0] * ESCALATE_N
    buckets = [bucket(i, mom) for i, mom in enumerate(moments)]

    actions = _run(buckets, fallback=weak)

    assert _kinds(actions)[:2] == [ACTION_OPEN_WARNING, ACTION_ESCALATE]
    assert actions[1].evidence["enough_data"] is False


def test_active_alarm_is_not_rechecked() -> None:
    buckets = _series((ESCALATE_N + 4, MOM_ALARM))

    actions = _run(buckets, active=_active("alarm"))

    assert _kinds(actions) == [ACTION_CONFIRM]


# --- База и восстановление ----------------------------------------------------


def test_frozen_base_prevents_false_recovery() -> None:
    """Плавающая база сползла к аварийному моменту (18), замороженная — нет."""
    buckets = _series((40, MOM_ALARM))

    actions = _run(buckets, active=_active("alarm"), fallback=18.0)

    assert ACTION_NORMALIZE not in _kinds(actions)


def test_without_frozen_base_falls_back_to_sliding() -> None:
    """Эпизоду не из чего посчитать базу — восстановление по базе прогона."""
    buckets = _series((incident_config.RECOVER_SUSTAIN_BUCKETS, MOM_OK))

    actions = _run(buckets, active=_active("alarm", base=None), fallback=BASE)

    assert _kinds(actions) == [ACTION_NORMALIZE]


def test_recovery_uses_bucket_median_not_min() -> None:
    pulsing = _bucket(0, MOM_OK, mom_min=MOM_OK * 0.5)

    assert is_recovered(pulsing, BASE)


def test_frozen_base_ignores_stopped_samples() -> None:
    buckets = _series((4, MOM_OK))
    buckets += [_bucket(4 + i, 0.0, spd=0.0) for i in range(4)]

    base, samples = frozen_base(buckets, T0 + 8 * SPAN)

    assert base == MOM_OK
    assert samples == 4 * SAMPLES


def test_active_warning_confirmed_and_normalized() -> None:
    recover_n = incident_config.RECOVER_SUSTAIN_BUCKETS
    buckets = _series((2, MOM_WARN), (recover_n, MOM_OK))

    actions = _run(buckets, active=_active("warning"))

    # Подтверждения до нормализации схлопываются: остаётся только закрытие.
    assert _kinds(actions) == [ACTION_NORMALIZE]


def test_recovery_below_sustain_keeps_active() -> None:
    buckets = _series(
        (2, MOM_WARN),
        (incident_config.RECOVER_SUSTAIN_BUCKETS - 1, MOM_OK),
    )

    actions = _run(buckets, active=_active("alarm"))

    # Восстановление не добрало сустейн — эпизод жив, последний CONFIRM
    # указывает на последнюю аномальную корзину.
    assert _kinds(actions) == [ACTION_CONFIRM]
    assert actions[0].at == T0 + 2 * SPAN


# --- Закрытие ремонтом и давностью ---------------------------------------------


def test_repair_start_closes_episode() -> None:
    buckets = _series((20, MOM_ALARM))
    repair = T0 + 5 * SPAN

    actions = _run(buckets, active=_active("alarm"), repairs=[RepairWindow(repair)])

    # Пока ремонт идёт, новый эпизод не открывается — даже при низком моменте.
    assert _kinds(actions) == [ACTION_NORMALIZE]
    assert actions[0].at == repair
    assert actions[0].close_reason == CLOSE_REASON_REPAIR


def test_repair_start_inside_bucket_closes_before_it() -> None:
    """Ремонт начался внутри корзины — она уже не эскалирует эпизод."""
    buckets = _series((OK_BEFORE, MOM_OK), (ESCALATE_N + 4, MOM_ALARM))
    t0 = T0 + OK_BEFORE * SPAN
    repair = t0 + timedelta(hours=4) - SPAN / 2

    actions = _run(buckets, repairs=[RepairWindow(repair)])

    assert _kinds(actions) == [ACTION_OPEN_WARNING, ACTION_NORMALIZE]
    assert actions[1].at == repair


def test_new_episode_only_after_repair_ends() -> None:
    buckets = _series((20, MOM_ALARM))
    repair = RepairWindow(T0 + 5 * SPAN, T0 + 8 * SPAN)

    actions = _run(buckets, active=_active("alarm"), repairs=[repair])

    kinds = _kinds(actions)
    assert kinds[0] == ACTION_NORMALIZE
    reopen = next(a for a in actions if a.kind == ACTION_OPEN_WARNING)
    # Серия набирается заново после конца ремонта.
    assert reopen.at == repair.end


def test_repair_closes_silent_station_by_wall_clock() -> None:
    repair = T0 + timedelta(hours=10)

    actions = _run(
        [],
        active=_active("alarm"),
        repairs=[RepairWindow(repair)],
        now=T0 + timedelta(hours=12),
    )

    assert _kinds(actions) == [ACTION_NORMALIZE]
    assert actions[0].at == repair
    assert actions[0].close_reason == CLOSE_REASON_REPAIR


def test_repair_finished_before_opening_does_not_close() -> None:
    buckets = _series((4, MOM_ALARM))

    actions = _run(
        buckets,
        active=_active("alarm", opened=2),
        repairs=[RepairWindow(T0, T0 + SPAN)],
        now=T0 + timedelta(hours=2),
    )

    assert ACTION_NORMALIZE not in _kinds(actions)


def test_repair_running_at_opening_closes_at_once() -> None:
    """Ремонт загрузили позже: тревога при пуске в ремонте закрывается T0."""
    buckets = _series((4, MOM_ALARM))

    actions = _run(
        buckets,
        active=_active("warning", opened=2),
        repairs=[RepairWindow(T0)],
    )

    assert actions[0].kind == ACTION_NORMALIZE
    assert actions[0].at == T0 + 2 * SPAN
    assert actions[0].close_reason == CLOSE_REASON_REPAIR


def test_no_opening_while_repair_runs() -> None:
    buckets = _series((OK_BEFORE, MOM_OK), (8, MOM_ALARM))

    actions = _run(buckets, repairs=[RepairWindow(T0 + OK_BEFORE * SPAN)])

    assert actions == []


def test_no_opening_before_previous_closure() -> None:
    """Прошлый эпизод закрылся (ремонтом задним числом) — серия до этого не в счёт."""
    buckets = _series((OK_BEFORE, MOM_OK), (10, MOM_ALARM))
    floor = T0 + (OK_BEFORE + 2) * SPAN

    actions = _run(buckets, floor=floor)

    assert actions[0].kind == ACTION_OPEN_WARNING
    assert actions[0].at == floor


def test_stale_after_fourteen_days() -> None:
    now = T0 + timedelta(days=incident_config.STALE_DAYS, hours=3)

    actions = _run([], active=_active("alarm"), now=now)

    assert _kinds(actions) == [ACTION_NORMALIZE]
    assert actions[0].at == T0 + timedelta(days=incident_config.STALE_DAYS)
    assert actions[0].close_reason == CLOSE_REASON_STALE


# --- Курсор и догон -------------------------------------------------------------


def test_series_across_cursor_boundary_opens() -> None:
    """Серия началась до курсора, сустейн добрался после — эпизод открывается."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((OK_BEFORE, MOM_OK), (warn_n, MOM_WARN))
    # Курсор — перед последней корзиной серии: (warn_n - 1) корзин уже видели.
    cursor_ts = buckets[OK_BEFORE + warn_n - 1].start_ts

    actions = _run(buckets, cursor_ts=cursor_ts)

    assert _kinds(actions) == [ACTION_OPEN_WARNING]
    assert actions[0].at == T0 + OK_BEFORE * SPAN


def test_buckets_before_cursor_never_reopen() -> None:
    """Старая (уже обработанная) серия не даёт действий при повторном чтении."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    buckets = _series((OK_BEFORE, MOM_OK), (warn_n, MOM_WARN), (2, MOM_OK))
    cursor_ts = buckets[-1].start_ts + SPAN  # всё уже оценено

    assert _run(buckets, cursor_ts=cursor_ts) == []


def test_catchup_full_history_in_one_pass() -> None:
    """Догон после простоя: открыть, эскалировать, закрыть и открыть снова."""
    warn_n = incident_config.WARN_SUSTAIN_BUCKETS
    recover_n = incident_config.RECOVER_SUSTAIN_BUCKETS
    buckets = _series(
        (OK_BEFORE, MOM_OK),
        (ESCALATE_N + 2, MOM_ALARM),
        (recover_n, MOM_OK),
        (warn_n, MOM_WARN),
    )

    actions = _run(buckets)

    assert _kinds(actions) == [
        ACTION_OPEN_WARNING,
        ACTION_ESCALATE,
        ACTION_NORMALIZE,
        ACTION_OPEN_WARNING,
    ]


# --- Перерыв связи -------------------------------------------------------------


def _with_gap(after_gap: list[float]) -> list[Bucket2h]:
    """Провал в T0, связи нет с T0 + 45 мин до T0 + 5 ч, затем ``after_gap``."""
    buckets = _series((OK_BEFORE, MOM_OK), (3, MOM_ALARM))
    resume = OK_BEFORE + 5 * PER_HOUR
    buckets += [_bucket(resume + i, mom) for i, mom in enumerate(after_gap)]
    return buckets


def test_no_escalation_on_partial_hour_after_gap() -> None:
    actions = _run(_with_gap([MOM_OK]))

    assert ACTION_ESCALATE not in _kinds(actions)


def test_escalates_after_full_hour_of_low_moment_after_gap() -> None:
    actions = _run(_with_gap([MOM_ALARM] * PER_HOUR))

    assert ACTION_ESCALATE in _kinds(actions)


def test_recovered_after_gap_does_not_escalate() -> None:
    actions = _run(_with_gap([MOM_OK] * (PER_HOUR + 2)))

    assert ACTION_ESCALATE not in _kinds(actions)


# --- Пересчёт стадий истории ----------------------------------------------------


def test_stage_escalation_for_history() -> None:
    buckets = _series((OK_BEFORE, MOM_OK), (ESCALATE_N + 4, MOM_ALARM))
    t0 = T0 + OK_BEFORE * SPAN
    base, samples = frozen_base(buckets, t0)

    action = stage_escalation(
        buckets,
        opened_at=t0,
        until=buckets[-1].start_ts + SPAN,
        base=base,
        base_samples=samples,
    )

    assert action is not None
    assert action.at == t0 + timedelta(hours=incident_config.STAGE_ESCALATE_HOURS)


def test_stage_escalation_none_when_closed_before_four_hours() -> None:
    buckets = _series((OK_BEFORE, MOM_OK), (ESCALATE_N + 4, MOM_ALARM))
    t0 = T0 + OK_BEFORE * SPAN
    base, samples = frozen_base(buckets, t0)

    action = stage_escalation(
        buckets,
        opened_at=t0,
        until=t0 + timedelta(hours=3),
        base=base,
        base_samples=samples,
    )

    assert action is None


def test_sparse_station_twelve_samples_per_hour_still_decides() -> None:
    """Станция пишет раз в 5 минут (3 отсчёта в корзине) — час полный."""
    sparse = 3

    def bucket(idx: int, mom: float) -> Bucket2h:
        return Bucket2h(
            start_ts=T0 + idx * SPAN,
            mom_min=mom,
            spd=SPD,
            mom_med24=MED,
            spd_med24=SPD,
            mom_med=mom,
            mom_sum=mom * sparse,
            mom_n=sparse,
            work_sum=mom * sparse,
            work_n=sparse,
        )

    moments = [MOM_OK] * 30 + [MOM_ALARM] * (ESCALATE_N + 2)
    actions = _run([bucket(i, mom) for i, mom in enumerate(moments)])

    assert ACTION_ESCALATE in _kinds(actions)
