"""Факт нефти с начала месяца: медиана замеров в работе × доля работы."""

# Времена в тестах — местные наивные, как в данных.
# ruff: noqa: DTZ001

from datetime import datetime

from apps.wells.services.month_production import (
    MonthMeasurement,
    abai_work_share,
    month_oil_fact,
    month_start,
)

NOW = datetime(2026, 10, 20, 12, 0)
FAR = datetime(3333, 12, 31)


def _m(
    day: int,
    oil: float | None,
    liquid: float | None,
    month: int = 10,
) -> MonthMeasurement:
    return MonthMeasurement(datetime(2026, month, day, 3, 0), oil, liquid)


def test_month_start() -> None:
    assert month_start(NOW) == datetime(2026, 10, 1)


def test_median_in_work_times_work_share() -> None:
    # Выброс 15,7 медиану не сдвигает; простой — половина времени.
    measurements = [_m(2, 5.0, 27), _m(5, 5.5, 27), _m(9, 15.7, 36), _m(12, 4.6, 26)]
    fact = month_oil_fact(
        measurements,
        now=NOW,
        work_share=0.5,
        work_share_source="sdmo",
    )
    assert fact.oil_rate_working == 5.25
    assert fact.oil_rate == 2.62
    assert fact.work_share == 0.5
    assert fact.work_share_source == "sdmo"
    assert fact.measurements == 4
    assert fact.period_from == datetime(2026, 10, 2, 3, 0)


def test_stopped_measurements_and_duplicates_are_skipped() -> None:
    measurements = [
        _m(2, 5.0, 27),
        _m(2, 5.0, 27),  # дубль того же замера
        _m(4, 0.0, 0.0),  # скважина стояла — жидкости нет
        _m(6, None, 20),  # нефть не определена
        _m(8, 6.0, 28),
        _m(10, 7.0, 29),
    ]
    fact = month_oil_fact(
        measurements,
        now=NOW,
        work_share=1.0,
        work_share_source="abai",
    )
    assert fact.measurements == 3
    assert fact.oil_rate_working == 6.0
    assert fact.oil_rate == 6.0


def test_few_measurements_take_last_three_from_previous_month() -> None:
    now = datetime(2026, 11, 2, 9, 0)
    measurements = [_m(25, 5.0, 27), _m(28, 6.0, 27), _m(1, 7.0, 27, month=11)]
    fact = month_oil_fact(
        measurements,
        now=now,
        work_share=1.0,
        work_share_source="sdmo",
    )
    assert fact.measurements == 3
    assert fact.oil_rate_working == 6.0
    assert fact.period_from == datetime(2026, 10, 25, 3, 0)


def test_unknown_work_share_keeps_working_rate_only() -> None:
    fact = month_oil_fact(
        [_m(2, 5.0, 27), _m(5, 5.0, 27), _m(8, 5.0, 27)],
        now=NOW,
        work_share=None,
        work_share_source=None,
    )
    assert fact.oil_rate is None
    assert fact.oil_rate_working == 5.0


def test_no_measurements_in_work() -> None:
    fact = month_oil_fact(
        [_m(5, 0.0, 0.0)],
        now=NOW,
        work_share=0.0,
        work_share_source="sdmo",
    )
    assert fact.oil_rate is None
    assert fact.oil_rate_working is None
    assert fact.measurements == 0


def test_abai_work_share_by_covered_time() -> None:
    since, until = datetime(2026, 10, 1), datetime(2026, 10, 21)
    intervals = [
        (1, datetime(2026, 9, 2), datetime(2026, 10, 11), "WRK", "В работе", None),
        (1, datetime(2026, 10, 11), FAR, "DWN", "В простое", "Нет подачи"),
    ]
    assert abai_work_share(intervals, since=since, until=until) == 0.5
    periodic = [(1, datetime(2026, 9, 1), FAR, "PEXP", "Периодическая", None)]
    assert abai_work_share(periodic, since=since, until=until) == 1.0
    assert abai_work_share([], since=since, until=until) is None
