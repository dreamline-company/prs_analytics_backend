"""Тесты правила R10: события 1–4, простои ABAI, признак СУ, техрежим на дату.

Соответствие скрипту автора целиком проверено отдельно — прогоном обеих
реализаций на данных прода за пять дат (расхождений нет). Здесь — ветки
правила на синтетике, чтобы правки не ломали их молча.
"""

from datetime import date, timedelta

from apps.detectors.cits_events import config
from apps.detectors.cits_events.rule import (
    EVENT_DROP,
    EVENT_NULL,
    EVENT_STALE,
    EVENT_ZERO,
    KLASS_DROP,
    KLASS_ZERO_PERIODIC,
    SERVICE_CHRONIC_HIGH,
    SERVICE_CHRONIC_LOW,
    SERVICE_NO_REGIME,
    SERVICE_NULL_LIQUID,
    SERVICE_PLAN_MISMATCH,
    SERVICE_REGIME_OUTDATED,
    SERVICE_STATUS_MISMATCH,
    SU_NONE,
    SU_PARTIAL,
    SU_RUN,
    SU_STOP,
    MappingSu,
    Measurement,
    RegimeInterval,
    StatusInterval,
    SuDay,
    WellInput,
    evaluate,
    recent_days,
    rezhim_on,
    su_requests,
)

FIX = date(2026, 9, 18)
RUN = SuDay(288, 288)
STOP = SuDay(288, 0)


def _day(offset: int) -> date:
    """Сутки относительно даты фиксации: 0 — сама дата, -1 — накануне."""
    return FIX + timedelta(days=offset)


def _series(values: list[float | None], *, last: int = 0) -> list[Measurement]:
    """Ежедневные замеры, последний — на сутки ``last``."""
    first = last - len(values) + 1
    return [Measurement(_day(first + i), q) for i, q in enumerate(values)]


def _well(
    values: list[float | None],
    *,
    last: int = 0,
    regime: float | None = 30.0,
    statuses: list[StatusInterval] | None = None,
) -> WellInput:
    regimes = [] if regime is None else [RegimeInterval(_day(-40), regime)]
    return WellInput(
        name="UZK_0377",
        series=_series(values, last=last),
        regimes=regimes,
        statuses=statuses or [],
    )


def _status(
    code: str,
    start: int,
    end: int | None,
    reason: str | None = None,
) -> StatusInterval:
    return StatusInterval(
        start=_day(start),
        end=None if end is None else _day(end),
        code=code,
        name={"WRK": "В работе", "DWN": "В простое", "PEXP": "Период."}[code],
        reason=reason,
    )


def _su(days: dict[date, SuDay] | None = None) -> MappingSu:
    return MappingSu(days or {})


def test_fresh_drop_is_event_1() -> None:
    well = _well([30.0] * 12 + [15.0, 14.0, 12.0])

    verdict = evaluate(well, FIX, _su())

    event = verdict.event
    assert event is not None
    assert event.event == EVENT_DROP
    assert event.klass == KLASS_DROP
    assert event.n_series == 3
    assert event.series_from == _day(-2)
    assert event.dev == 12.0 / 30.0 - 1
    assert event.prev_dev == 0.0
    assert verdict.incident_event is event
    assert verdict.tail_bad is True


def test_two_low_measurements_are_not_enough() -> None:
    verdict = evaluate(_well([30.0] * 12 + [15.0, 14.0]), FIX, _su())

    assert verdict.event is None
    assert verdict.tail_bad is True


def test_drop_older_than_fresh_window_goes_to_chronic() -> None:
    well = _well([30.0] * 12 + [15.0] * (config.FRESH_D + 2))

    verdict = evaluate(well, FIX, _su())

    assert verdict.event is None
    assert [item.kind for item in verdict.service] == [SERVICE_CHRONIC_LOW]
    assert verdict.chronic is True


def test_drop_without_norm_before_is_chronic() -> None:
    # До серии медиана −25 %: нормы не было, событие 1 не открывается.
    verdict = evaluate(_well([22.5] * 12 + [15.0] * 3), FIX, _su())

    assert verdict.event is None
    assert [item.kind for item in verdict.service] == [SERVICE_CHRONIC_LOW]


def test_drop_after_measurements_far_above_plan_is_plan_mismatch() -> None:
    verdict = evaluate(_well([60.0] * 12 + [15.0] * 3), FIX, _su())

    assert verdict.event is None
    assert [item.kind for item in verdict.service] == [SERVICE_PLAN_MISMATCH]


def test_chronic_high() -> None:
    verdict = evaluate(_well([50.0] * 15), FIX, _su())

    assert [item.kind for item in verdict.service] == [SERVICE_CHRONIC_HIGH]
    assert verdict.tail_bad is False


def test_new_zero_series_with_running_su() -> None:
    well = _well([30.0] * 12 + [0.0, 0.2])

    verdict = evaluate(well, FIX, _su({_day(-1): RUN, _day(0): RUN}))

    event = verdict.event
    assert event is not None
    assert event.event == EVENT_ZERO
    assert event.klass == f"нулевые замеры, {SU_RUN}"
    assert event.su == SU_RUN
    assert event.series_from == _day(-1)
    assert event.n_series == 2
    assert "доля работы СУ в дни нулей: 100%, 100%" in event.note


def test_zero_series_su_labels() -> None:
    well = _well([30.0] * 12 + [0.0, 0.0])

    def label(days: dict[date, SuDay]) -> str | None:
        event = evaluate(well, FIX, _su(days)).event
        assert event is not None
        return event.su

    assert label({}) == SU_NONE
    assert label({_day(-1): RUN, _day(0): STOP}) == SU_STOP
    assert label({_day(-1): STOP, _day(0): RUN}) == SU_PARTIAL
    # Меньше SU_MIN_RECORDS записей за сутки — сутки без данных.
    assert label({_day(-1): SuDay(10, 10), _day(0): SuDay(10, 10)}) == SU_NONE


def test_zeros_alternating_with_supply_are_routine() -> None:
    well = _well([30.0, 0.0, 30.0, 0.0, 30.0, 30.0, 0.0, 30.0, 30.0, 30.0, 0.0, 0.0])

    event = evaluate(well, FIX, _su()).event

    assert event is not None
    assert event.klass.startswith("замеры неустойчивые")


def test_zeros_of_periodic_well() -> None:
    well = _well(
        [30.0] * 12 + [0.0, 0.0],
        statuses=[_status("PEXP", -30, None)],
    )

    event = evaluate(well, FIX, _su()).event

    assert event is not None
    assert event.klass == KLASS_ZERO_PERIODIC
    assert "периодическая эксплуатация" in event.note


def test_stale_measurement_split_by_su() -> None:
    well = _well([30.0] * 10, last=-(config.STALE_D + 1))
    stopped = dict.fromkeys(recent_days(FIX), STOP)

    verdict = evaluate(well, FIX, _su(stopped))

    event = verdict.event
    assert event is not None
    assert event.event == EVENT_STALE
    assert event.klass == f"замер устарел, {SU_STOP}"
    assert event.age_d == config.STALE_D + 1
    assert verdict.stale is True


def test_periodic_well_has_longer_stale_threshold() -> None:
    periodic = [_status("PEXP", -60, None)]
    fresh_enough = _well(
        [30.0] * 10,
        last=-(config.STALE_D + 1),
        statuses=periodic,
    )
    stale = _well(
        [30.0] * 10,
        last=-(config.STALE_D_PERIODIC + 1),
        statuses=periodic,
    )

    assert evaluate(fresh_enough, FIX, _su()).event is None
    event = evaluate(stale, FIX, _su()).event
    assert event is not None
    assert event.event == EVENT_STALE


def test_empty_last_measurement_is_event_4() -> None:
    verdict = evaluate(_well([30.0] * 10 + [None]), FIX, _su())

    event = verdict.event
    assert event is not None
    assert event.event == EVENT_NULL
    assert [item.kind for item in verdict.service] == [SERVICE_NULL_LIQUID]


def test_well_idle_on_fix_date_is_not_evaluated() -> None:
    well = _well(
        [30.0] * 12 + [0.0, 0.0],
        statuses=[_status("DWN", -3, None, "ПРС")],
    )

    verdict = evaluate(well, FIX, _su())

    assert verdict.idle is True
    assert verdict.event is None
    assert verdict.service == ()


def test_multi_day_idle_days_are_cut_from_series() -> None:
    # Нули пришлись на простой с −5 по −3: после него подача нормальная.
    well = _well(
        [30.0] * 10 + [0.0, 0.0, 0.0, 30.0, 30.0, 30.0],
        statuses=[_status("DWN", -5, -3, "КРС")],
    )

    verdict = evaluate(well, FIX, _su())

    assert verdict.event is None
    assert verdict.tail_bad is False


def test_same_day_idle_goes_to_note_only() -> None:
    well = _well(
        [30.0] * 12 + [15.0, 14.0, 12.0],
        statuses=[_status("DWN", 0, 0, "Внеплановое отключение ЭЭ")],
    )

    event = evaluate(well, FIX, _su()).event

    assert event is not None
    assert event.event == EVENT_DROP
    assert event.note == "простой в сутки замера: Внеплановое отключение ЭЭ"


def test_idle_in_abai_while_su_runs_is_status_mismatch() -> None:
    well = _well([30.0] * 10, statuses=[_status("DWN", -10, None, "ПРС")])
    running = dict.fromkeys(recent_days(FIX), RUN)

    verdict = evaluate(well, FIX, _su(running))

    assert verdict.idle is True
    assert [item.kind for item in verdict.service] == [SERVICE_STATUS_MISMATCH]


def test_regime_on_measurement_date() -> None:
    regimes = [
        RegimeInterval(date(2026, 8, 1), 35.0),
        RegimeInterval(date(2026, 9, 1), 30.0),
    ]

    assert rezhim_on(regimes, date(2026, 8, 31))[0] == 35.0
    assert rezhim_on(regimes, date(2026, 9, 1))[0] == 30.0
    assert rezhim_on(regimes, date(2026, 7, 31))[1] == SERVICE_NO_REGIME
    too_old = date(2026, 9, 1) + timedelta(days=config.TM_MAX_AGE_D + 1)
    assert rezhim_on(regimes, too_old)[1] == SERVICE_REGIME_OUTDATED


def test_without_regime_goes_to_service() -> None:
    verdict = evaluate(_well([30.0] * 10, regime=None), FIX, _su())

    assert [item.kind for item in verdict.service] == [SERVICE_NO_REGIME]
    assert verdict.tail_bad is None


def test_su_requests_cover_zero_series_and_recent_days() -> None:
    zeros = _well([30.0] * 12 + [0.0, 0.0])
    stale = WellInput(
        name="UZK_0999",
        series=_series([30.0] * 5, last=-20),
        regimes=[RegimeInterval(_day(-40), 30.0)],
        statuses=[],
    )
    normal = WellInput(
        name="UZK_0001",
        series=_series([30.0] * 15),
        regimes=[RegimeInterval(_day(-40), 30.0)],
        statuses=[],
    )

    requests = su_requests([zeros, stale, normal], FIX)

    assert requests == {
        "UZK_0377": {_day(-1), _day(0)},
        "UZK_0999": set(recent_days(FIX)),
    }
