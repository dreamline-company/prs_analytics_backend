"""Динамика МРП по месяцам: ряд, скользящее окно, согласованность с КМГ."""

from datetime import datetime

import pytest

from apps.repairs.services.mrp import (
    DynamicsPoint,
    RepairSpan,
    WellFundInput,
    compute_kmg,
    compute_kmg_dynamics,
    compute_month_stats,
    window_months,
)

NOW = datetime(2026, 10, 14, 10, 0)  # noqa: DTZ001


def _dt(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _span(
    repair_id: int,
    start: str,
    end: str | None,
    *,
    gtm: bool = False,
) -> RepairSpan:
    return RepairSpan(repair_id, _dt(start), _dt(end) if end else None, gtm)


def _wells() -> list[WellFundInput]:
    return [
        WellFundInput(
            well_id=1,
            name="VMB_0001",
            oil_field="VMB",
            lift_type=None,
            work_intervals=[(_dt("2024-01-01T00:00"), _dt("2027-01-01T00:00"))],
            repairs=[
                _span(1, "2025-11-10T00:00", "2025-11-12T00:00"),
                _span(2, "2026-03-05T00:00", "2026-03-06T00:00", gtm=True),
                _span(3, "2026-06-20T00:00", "2026-06-22T00:00"),
                _span(4, "2026-09-02T00:00", "2026-09-03T00:00"),
            ],
        ),
        WellFundInput(
            well_id=2,
            name="JLM_0002",
            oil_field="JLM",
            lift_type=None,
            work_intervals=[(_dt("2026-02-01T00:00"), _dt("2027-01-01T00:00"))],
            repairs=[_span(5, "2026-04-10T00:00", "2026-04-11T00:00")],
        ),
    ]


def _series(points: int = 12, window: int = 12) -> list[DynamicsPoint]:
    return compute_kmg_dynamics(
        _wells(),
        end_year=2026,
        end_month=9,
        points=points,
        window_months_count=window,
        now=NOW,
    )


def test_series_has_requested_months_in_order() -> None:
    series = _series(points=6)

    assert [p.month for p in series] == [
        "2026-04",
        "2026-05",
        "2026-06",
        "2026-07",
        "2026-08",
        "2026-09",
    ]


def test_last_point_equals_kmg_for_the_same_window() -> None:
    last = _series()[-1]
    total = compute_kmg(_wells(), end_year=2026, end_month=9, now=NOW).total

    assert last.rolling_work_days == pytest.approx(total.work_days)
    assert last.rolling_repairs == total.repairs
    assert last.rolling_gtm == total.gtm
    assert last.rolling_avg_fund == pytest.approx(total.avg_fund)
    assert last.rolling_mrp_with_gtm == pytest.approx(total.mrp_with_gtm)
    assert last.rolling_mrp_without_gtm == pytest.approx(total.mrp_without_gtm)


def test_every_point_equals_kmg_for_its_own_window() -> None:
    for point in _series(points=5):
        year, month = (int(x) for x in point.month.split("-"))
        total = compute_kmg(_wells(), end_year=year, end_month=month, now=NOW).total

        assert point.rolling_repairs == total.repairs
        assert point.rolling_work_days == pytest.approx(total.work_days)


def test_month_values_are_for_the_month_only() -> None:
    by_month = {p.month: p for p in _series()}

    # март 2026: один ремонт (ГТМ) у VMB_0001; фонд — обе скважины (JLM с февраля)
    march = by_month["2026-03"]
    assert march.month_repairs == 1
    assert march.month_gtm == 1
    assert march.month_fund == 2
    # апрель: ремонт JLM_0002 (отказ)
    april = by_month["2026-04"]
    assert april.month_repairs == 1
    assert april.month_gtm == 0
    assert april.month_mrp_without_gtm == pytest.approx(april.month_work_days)


def test_month_without_repairs_has_undefined_mrp() -> None:
    by_month = {p.month: p for p in _series()}

    july = by_month["2026-07"]
    assert july.month_repairs == 0
    assert july.month_mrp_with_gtm is None
    assert july.month_mrp_without_gtm is None
    # а скользящий показатель за год определён
    assert july.rolling_mrp_with_gtm is not None


def test_all_repairs_gtm_gives_undefined_mrp_without_gtm() -> None:
    by_month = {p.month: p for p in _series()}

    march = by_month["2026-03"]  # единственный ремонт месяца — ГТМ
    assert march.month_mrp_with_gtm is not None
    assert march.month_mrp_without_gtm is None


def test_repair_time_is_subtracted_from_work_time() -> None:
    stats = compute_month_stats(
        _wells(),
        window_months(2026, 6, 1),
        now=NOW,
    )

    # июнь 2026: 30 сут у VMB_0001 минус ремонт 20–22 июня (2 сут); JLM_0002 — 30
    assert stats[0].work_days == pytest.approx(28 + 30)
    assert stats[0].fund == 2
    assert stats[0].calendar_days == pytest.approx(60)


def test_window_length_is_configurable() -> None:
    series = _series(points=3, window=6)

    last = series[-1]
    total = compute_kmg(
        _wells(),
        end_year=2026,
        end_month=9,
        months=6,
        now=NOW,
    ).total
    assert last.rolling_repairs == total.repairs
    assert last.rolling_work_days == pytest.approx(total.work_days)


def test_empty_fund() -> None:
    series = compute_kmg_dynamics([], end_year=2026, end_month=9, points=3, now=NOW)

    assert len(series) == 3
    assert all(p.rolling_mrp_with_gtm is None for p in series)
    assert all(p.month_fund == 0 for p in series)
