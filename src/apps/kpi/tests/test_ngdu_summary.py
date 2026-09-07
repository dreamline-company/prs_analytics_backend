"""Свёртка сводки НГДУ: пороги и границы без базы."""

from datetime import datetime

from apps.kpi.use_cases.get_ngdu_summary import WellSummaryInput, summarize

AS_OF = datetime(2026, 9, 8, 12, 0)  # noqa: DTZ001 — наивный UTC, как в БД


def _row(
    well_id: int,
    fact: float | None,
    plan: float | None,
    *,
    active: bool = False,
) -> WellSummaryInput:
    return WellSummaryInput(
        well_id=well_id,
        oil_fact=fact,
        oil_plan=plan,
        is_active=active,
    )


def test_empty_fund() -> None:
    summary = summarize([], as_of=AS_OF)

    assert summary.wells.total == 0
    assert summary.oil_production.value == 0
    assert summary.plan_fulfillment.percent is None
    assert summary.deviations.wells == 0


def test_production_counts_every_fresh_measurement_but_plan_needs_both() -> None:
    rows = [
        _row(1, fact=10.0, plan=10.0, active=True),  # в плане
        _row(2, fact=5.0, plan=None),  # замер есть, плана нет -> только в добыче
        _row(3, fact=None, plan=20.0),  # план есть, замера нет -> нигде
        _row(4, fact=8.0, plan=0.0),  # нулевой план не сравнивается
    ]

    summary = summarize(rows, as_of=AS_OF)

    assert summary.wells.total == 4
    assert summary.wells.active == 1
    assert summary.oil_production.value == 23.0
    assert summary.oil_production.wells_measured == 3
    assert summary.plan_fulfillment.wells == 1
    assert summary.plan_fulfillment.fact == 10.0
    assert summary.plan_fulfillment.plan == 10.0
    assert summary.plan_fulfillment.percent == 100.0


def test_deviation_threshold_and_losses() -> None:
    rows = [
        _row(1, fact=9.0, plan=10.0),  # -10% ровно на пороге -> не отклонение
        _row(2, fact=8.9, plan=10.0),  # ниже порога -> отклонение, потери 1.1
        _row(3, fact=5.0, plan=20.0),  # отклонение, потери 15
        _row(4, fact=30.0, plan=20.0),  # перевыполнение не считается
    ]

    summary = summarize(rows, as_of=AS_OF)

    assert summary.deviations.wells == 2
    assert summary.deviations.losses == 16.1
    assert summary.deviations.threshold_percent == 10.0
    assert summary.plan_fulfillment.percent == round((9 + 8.9 + 5 + 30) / 60 * 100, 1)
