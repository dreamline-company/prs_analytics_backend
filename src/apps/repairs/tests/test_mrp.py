"""МРП: по скважине (до/после ремонта) и по методике КМГ — без базы."""

from datetime import datetime

import pytest

from apps.repairs.services.mrp import (
    RepairSpan,
    WellFundInput,
    compute_kmg,
    compute_well_mrp,
    interpret_well_mrp,
    window_months,
)

NOW = datetime(2026, 10, 2, 12, 42)  # noqa: DTZ001


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


# Реальные ремонты VMB_0418 (ID из API), проверено вручную по датам
VMB_0418 = [
    _span(62871, "2026-06-16T12:20", "2026-06-19T11:10"),
    _span(63176, "2026-08-01T10:54", "2026-08-02T10:05"),
    _span(63406, "2026-09-09T11:45", "2026-09-10T10:00"),
    _span(63513, "2026-09-24T15:30", "2026-09-26T11:05"),
]


def test_well_mrp_before_after_and_verdicts() -> None:
    result = compute_well_mrp(VMB_0418, now=NOW)
    by_id = {r.repair_id: r for r in result.repairs}

    assert by_id[62871].before_days is None
    assert by_id[62871].verdict == "unknown"

    assert by_id[63176].before_days == pytest.approx(42.99, abs=0.01)
    assert by_id[63176].after_days == pytest.approx(38.07, abs=0.01)
    assert by_id[63176].verdict == "worse"

    assert by_id[63406].before_days == pytest.approx(38.07, abs=0.01)
    assert by_id[63406].after_days == pytest.approx(14.23, abs=0.01)
    assert by_id[63406].verdict == "worse"

    # последний ремонт: следующего нет, скважина работает меньше прежнего МРП
    assert by_id[63513].after_days is None
    assert by_id[63513].running_days == pytest.approx(6.07, abs=0.01)
    assert by_id[63513].verdict == "watching"


def test_well_mrp_average_and_counts() -> None:
    result = compute_well_mrp(VMB_0418, now=NOW)

    assert result.intervals_count == 3
    assert result.repairs_count == 4
    assert result.worse_count == 2
    assert result.avg_days == pytest.approx(31.76, abs=0.01)
    assert result.last_end == _dt("2026-09-26T11:05")


def test_running_longer_than_before_is_better() -> None:
    spans = [
        _span(1, "2026-01-01T00:00", "2026-01-02T00:00"),
        _span(2, "2026-01-12T00:00", "2026-01-13T00:00"),  # до ремонта 10 сут
    ]
    result = compute_well_mrp(spans, now=_dt("2026-03-01T00:00"))  # уже 47 сут

    assert result.repairs[1].verdict == "better"


def test_adjacent_records_are_merged_into_one_repair() -> None:
    # реальный случай VMB_0418: запись 4628 закончилась, а 25073 началась сразу
    spans = [
        _span(55721, "2010-02-21T15:00", "2010-02-22T09:00"),
        _span(4628, "2011-03-11T15:00", "2011-03-12T09:35"),
        _span(25073, "2011-03-12T09:35", "2011-03-13T09:00"),
        _span(25997, "2011-04-07T00:00", "2011-04-12T00:00"),
    ]
    result = compute_well_mrp(spans, now=NOW)

    assert result.repairs_count == 3
    assert result.intervals_count == 2
    merged = {r.repair_id for r in result.repairs if r.merged}
    assert merged == {4628, 25073}
    # у склеенных записей одинаковые значения МРП
    by_id = {r.repair_id: r for r in result.repairs}
    assert by_id[4628].after_days == by_id[25073].after_days
    assert by_id[4628].after_days == pytest.approx(24.625, abs=0.001)


def test_open_repair_has_no_after_and_no_running() -> None:
    spans = [
        _span(1, "2026-01-01T00:00", "2026-01-02T00:00"),
        _span(2, "2026-02-01T00:00", None),
    ]
    result = compute_well_mrp(spans, now=NOW)

    assert result.repairs[1].after_days is None
    assert result.repairs[1].running_days is None
    assert result.repairs[0].after_days == pytest.approx(30.0)


def test_overlapping_repairs_do_not_give_negative_interval() -> None:
    spans = [
        _span(1, "2026-01-01T00:00", "2026-01-10T00:00"),
        _span(2, "2026-01-05T00:00", "2026-01-06T00:00"),
    ]
    result = compute_well_mrp(spans, now=NOW)

    assert result.intervals_count == 0
    assert result.avg_days is None


def test_no_repairs() -> None:
    result = compute_well_mrp([], now=NOW)

    assert result.repairs == []
    assert result.avg_days is None
    assert result.last_end is None


def test_interpretation_is_computed_by_code() -> None:
    result = compute_well_mrp(VMB_0418, now=NOW)

    text = interpret_well_mrp("VMB_0418", result, repair_id=63406)

    assert "VMB_0418" in text
    assert "32 сут" in text  # средний МРП 31.76
    assert "3 интервала между 4 ремонтами" in text
    assert "сократился на 24 сут" in text  # 38.07 → 14.23
    assert "2 из 4" in text


def test_interpretation_without_history() -> None:
    result = compute_well_mrp(
        [_span(1, "2026-01-01T00:00", "2026-01-02T00:00")],
        now=NOW,
    )

    assert "недостаточно истории" in interpret_well_mrp("W", result)


# ---------------------------------------------------------------------------
# Методика КМГ
# ---------------------------------------------------------------------------


def test_window_months_rolls_over_year() -> None:
    months = window_months(2026, 9)

    assert months[0] == (2025, 10)
    assert months[-1] == (2026, 9)
    assert len(months) == 12


def _kmg_wells() -> list[WellFundInput]:
    well_a = WellFundInput(
        well_id=1,
        name="VMB_0001",
        oil_field="VMB",
        lift_type="ШГН",
        work_intervals=[(_dt("2025-01-01T00:00"), _dt("2027-01-01T00:00"))],
        repairs=[
            _span(10, "2025-09-30T00:00", "2025-10-02T00:00"),  # начат до окна
            _span(11, "2026-03-10T00:00", "2026-03-12T00:00"),  # отказ, 2 сут
            _span(12, "2026-06-01T00:00", "2026-06-02T00:00", gtm=True),  # ГТМ, 1 сут
        ],
    )
    well_b = WellFundInput(
        well_id=2,
        name="JLM_0002",
        oil_field="JLM",
        lift_type=None,
        work_intervals=[(_dt("2026-07-01T00:00"), _dt("2026-10-05T00:00"))],
        repairs=[
            _span(20, "2026-07-10T00:00", "2026-07-11T00:00"),
            _span(21, "2026-08-10T00:00", "2026-08-11T00:00"),
            _span(22, "2026-09-10T00:00", "2026-09-11T00:00"),
        ],
    )
    return [well_a, well_b]


def test_kmg_total_follows_the_form_formulas() -> None:
    result = compute_kmg(_kmg_wells(), end_year=2026, end_month=9, now=NOW)

    total = result.total
    assert result.window_start == _dt("2025-10-01T00:00")
    assert result.window_end == _dt("2026-10-01T00:00")
    # A: 365 − 1 (ремонт 10 с 01.10 по 02.10 — часть в окне) − 2 − 1;
    # B: 92 − 3
    assert total.work_days == pytest.approx(364 - 3 + 89, abs=1.0)
    assert total.repairs == 5  # ремонт 10 начат до окна — не считается
    assert total.gtm == 1
    assert total.failures == 4
    assert total.mrp_with_gtm == pytest.approx(total.work_days / 5)
    assert total.mrp_without_gtm == pytest.approx(total.work_days / 4)
    # фонд: A — 12 месяцев, B — 3 месяца
    assert total.avg_fund == pytest.approx(15 / 12)
    assert total.calendar_days == pytest.approx(365 + 92)


def test_kmg_groups_by_oil_field_and_lift_type() -> None:
    result = compute_kmg(_kmg_wells(), end_year=2026, end_month=9, now=NOW)

    assert {g.key for g in result.by_oil_field} == {"VMB", "JLM"}
    assert {g.key for g in result.by_lift_type} == {"ШГН", "не указан"}
    jlm = next(g for g in result.by_oil_field if g.key == "JLM")
    assert jlm.repairs == 3
    assert jlm.failures == 3


def test_crf_counts_only_non_gtm_repairs_from_threshold() -> None:
    result = compute_kmg(_kmg_wells(), end_year=2026, end_month=9, now=NOW)

    crf = result.crf
    assert crf.fund_at_end == 2
    assert [(w.name, w.repairs) for w in crf.wells] == [("JLM_0002", 3)]
    assert crf.repairs == 3
    assert crf.percent_of_fund == pytest.approx(50.0)
    # VMB_0001: 1 ремонт без ГТМ (ГТМ не считается); JLM_0002: 3
    assert crf.distribution == {1: 1, 3: 1}
    assert crf.wells_by_count == {3: ["JLM_0002"]}


def test_crf_threshold_is_configurable() -> None:
    result = compute_kmg(
        _kmg_wells(),
        end_year=2026,
        end_month=9,
        crf_min_repairs=1,
        now=NOW,
    )

    assert {w.name for w in result.crf.wells} == {"VMB_0001", "JLM_0002"}


def test_open_repair_cuts_work_time_until_now() -> None:
    well = WellFundInput(
        well_id=1,
        name="W_1",
        oil_field="W",
        lift_type=None,
        work_intervals=[(_dt("2026-09-01T00:00"), _dt("2026-10-31T00:00"))],
        repairs=[_span(1, "2026-09-20T00:00", None)],
    )

    result = compute_kmg(
        [well],
        end_year=2026,
        end_month=9,
        months=1,
        now=_dt("2026-09-25T00:00"),
    )

    # сентябрь: работа 1–20 (19 сут); с 20-го ремонт идёт до «сейчас» (25-го),
    # после него до конца месяца — снова работа
    assert result.total.work_days == pytest.approx(19 + 6, abs=1.0)


def test_empty_fund() -> None:
    result = compute_kmg([], end_year=2026, end_month=9, now=NOW)

    assert result.total.mrp_with_gtm is None
    assert result.total.avg_fund == 0
    assert result.crf.percent_of_fund is None
    assert result.by_oil_field == []


def test_no_work_time_gives_undefined_mrp_not_zero() -> None:
    # месторождение с ремонтами, но без времени работы (скважины не в работе)
    well = WellFundInput(
        well_id=1,
        name="SZK_0001",
        oil_field="SZK",
        lift_type=None,
        work_intervals=[],
        repairs=[_span(1, "2026-03-01T00:00", "2026-03-02T00:00")],
    )

    result = compute_kmg([well], end_year=2026, end_month=9, now=NOW)

    assert result.total.repairs == 1
    assert result.total.work_days == 0
    assert result.total.mrp_with_gtm is None
    assert result.total.mrp_without_gtm is None
