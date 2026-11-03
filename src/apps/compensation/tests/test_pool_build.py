"""Запись пула: защита от массового выпадения, очистка NaN, даты ПРС."""

from datetime import datetime

from apps.compensation.services.pool_data import DailyOil, finite, last_repairs
from apps.compensation.tasks.build_pool.build_pool import _clean, drop_guard

FAR = datetime(3333, 12, 31)  # noqa: DTZ001


def test_drop_guard() -> None:
    previous = set(range(20))

    assert drop_guard(previous, set(range(12))) is None  # выпало 8 из 20
    assert drop_guard(previous, set(range(5))) is not None  # выпало 15 из 20
    assert drop_guard(set(range(5)), set()) is None  # маленький пул не охраняем


def test_clean_replaces_nan_for_jsonb() -> None:
    row = {"oil": float("nan"), "tel": {"speed": float("inf"), "n": 5}, "x": [1.0]}

    assert _clean(row) == {"oil": None, "tel": {"speed": None, "n": 5}, "x": [1.0]}
    assert finite(float("nan")) is None
    assert finite(2.5) == 2.5


def test_last_repairs_merges_abai_and_revision() -> None:
    abai = [datetime(2026, 11, 1, 8, 0)]  # noqa: DTZ001 — свежий ПРС после ревизии
    revision = [datetime(2026, 5, 10), datetime(2026, 1, 3)]  # noqa: DTZ001

    starts = last_repairs(abai, revision)

    assert starts == [datetime(2026, 11, 1, 8, 0), datetime(2026, 5, 10)]  # noqa: DTZ001
    # Тот же день из двух источников — один ремонт.
    assert len(last_repairs([datetime(2026, 5, 10, 9)], revision)) == 2  # noqa: DTZ001


def test_daily_oil_sorts_rows_with_null_values() -> None:
    day = datetime(2026, 10, 1)  # noqa: DTZ001
    oil = DailyOil(
        liquid={1: [(day, FAR, None), (day, FAR, 30.0)]},
        water_cut={1: [(day, FAR, 50.0)]},
        density={1: 0.9},
    )

    assert oil.on(1, datetime(2026, 10, 5)) == 13.5  # noqa: DTZ001
