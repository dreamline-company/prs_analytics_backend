from datetime import date, datetime

from apps.repairs.services.mrp import (
    RepairSpan,
    WellFundInput,
    compute_kmg,
    compute_month_stats,
)
from apps.repairs.services.mrp_inputs import repair_fund_months_of

NOW = datetime(2026, 10, 2, 12, 42)  # noqa: DTZ001


def test_repair_month_counts_when_well_is_in_fund_at_start_or_end() -> None:
    firsts = [date(2026, 2, 1), date(2026, 3, 1), date(2026, 4, 1)]
    # в фонде на 1 марта и на 1 мая (после окна)
    fund = frozenset({(2026, 3), (2026, 5)})

    assert repair_fund_months_of(firsts, fund) == frozenset(
        {(2026, 2), (2026, 3), (2026, 4)},
    )


def test_december_looks_at_january_of_the_next_year() -> None:
    assert repair_fund_months_of(
        [date(2025, 12, 1)],
        frozenset({(2026, 1)}),
    ) == frozenset({(2025, 12)})


def test_well_out_of_fund_both_times_is_not_counted() -> None:
    assert repair_fund_months_of([date(2026, 2, 1)], frozenset()) == frozenset()


def _well(repair_months: frozenset[tuple[int, int]] | None) -> WellFundInput:
    return WellFundInput(
        well_id=1,
        name="W_1",
        oil_field="W",
        lift_type=None,
        work_intervals=[(datetime(2026, 2, 15), datetime(2026, 4, 1))],  # noqa: DTZ001
        repairs=[
            # ввод из консервации: скважина вошла в фонд в течение февраля
            RepairSpan(1, datetime(2026, 2, 10), datetime(2026, 2, 12)),  # noqa: DTZ001
        ],
        fund_months=frozenset({(2026, 3)}),
        repair_fund_months=repair_months,
    )


def test_repair_counted_when_well_joins_fund_during_the_month() -> None:
    well = _well(frozenset({(2026, 2), (2026, 3)}))

    feb, mar = compute_month_stats([well], [(2026, 2), (2026, 3)], now=NOW)

    assert (feb.repairs, mar.repairs) == (1, 0)
    # фонд месяца для времени работы и среднего фонда не меняется
    assert feb.fund == 0
    assert mar.fund == 1


def test_without_repair_months_the_fund_on_first_day_decides() -> None:
    well = _well(None)

    (feb,) = compute_month_stats([well], [(2026, 2)], now=NOW)

    assert feb.repairs == 0


def test_kmg_total_uses_repair_months() -> None:
    well = _well(frozenset({(2026, 2), (2026, 3)}))

    result = compute_kmg([well], end_year=2026, end_month=3, now=NOW)

    assert result.total.repairs == 1
