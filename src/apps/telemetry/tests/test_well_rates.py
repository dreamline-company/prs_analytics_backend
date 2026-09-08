"""Производные величины паспорта: обводнённость и отклонение от режима."""

from apps.telemetry.services.well_rates import oil_rate_deviation, water_cut


def test_oil_rate_deviation_sign_and_percent() -> None:
    assert oil_rate_deviation(oil_rate=8.0, plan_oil_rate=10.0) == (-2.0, -20.0)
    assert oil_rate_deviation(oil_rate=12.5, plan_oil_rate=10.0) == (2.5, 25.0)
    assert oil_rate_deviation(oil_rate=10.0, plan_oil_rate=10.0) == (0.0, 0.0)
    # процент от неокруглённой разницы
    assert oil_rate_deviation(oil_rate=0.0, plan_oil_rate=1.99) == (-2.0, -100.0)


def test_oil_rate_deviation_undefined_cases() -> None:
    assert oil_rate_deviation(oil_rate=None, plan_oil_rate=10.0) == (None, None)
    assert oil_rate_deviation(oil_rate=5.0, plan_oil_rate=None) == (None, None)
    # Нулевой план: разница есть, процента нет.
    assert oil_rate_deviation(oil_rate=5.0, plan_oil_rate=0.0) == (5.0, None)


def test_water_cut_bounds() -> None:
    assert water_cut(liquid_rate=100.0, oil_rate=40.0) == 60.0
    assert water_cut(liquid_rate=0.0, oil_rate=0.0) is None
    assert (
        water_cut(liquid_rate=10.0, oil_rate=20.0) == 0.0
    )  # мусорный замер зажимается
