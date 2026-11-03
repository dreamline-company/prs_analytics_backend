"""Пул доноров по методике: критерии, ступень, процентили, воронка."""

from dataclasses import replace

from apps.compensation.services.pool import (
    LIFT_EVN,
    LIFT_SHGN,
    FieldNorms,
    PoolInput,
    Telemetry,
    evaluate,
    field_norms,
    funnel,
    step_for,
)

TEL_SHGN = Telemetry(
    samples=800,
    k=0.10,
    moment_cv=None,
    moment_drift=None,
    fill_p5=80,
    speed=4.0,
)
TEL_EVN = Telemetry(
    samples=800,
    k=None,
    moment_cv=0.02,
    moment_drift=0.01,
    fill_p5=95,
    speed=140,
)
NORMS = FieldNorms(k_max={"UZK": 0.15, "VMB": 0.2}, pump_age_max={"UZK": 800.0})


def _well(**changes: object) -> PoolInput:
    base = PoolInput(
        well_id=1,
        name="UZK_0001",
        field="UZK",
        state="working",
        lift=LIFT_SHGN,
        days=27,
        oil=4.0,
        cv=0.1,
        flat=False,
        water_cut=60.0,
        pump_depth=900.0,
        hdin=500.0,
        mrp_days=400,
        since_repair_days=200,
        pump_age_days=300,
        telemetry=TEL_SHGN,
    )
    return replace(base, **changes)


def test_good_well_is_in_pool_with_step_by_submergence() -> None:
    result = evaluate(_well(), NORMS)

    assert result.in_pool
    assert result.passed
    assert result.has_telemetry
    assert result.submergence == 400
    assert result.step_percent == 15
    assert result.gain == 0.6


def test_step_by_submergence() -> None:
    assert [step_for(x) for x in (301, 300, 201, 151, 150, 100)] == [
        15,
        10,
        10,
        7,
        5,
        5,
    ]


def test_each_criterion_cuts() -> None:
    cases = {
        "state": _well(state="alarm"),
        "lift": _well(lift="УЭЦН"),
        "series": _well(days=24),
        "oil": _well(oil=2.4),  # ШГН: нужно ≥ 2,5
        "cv": _well(cv=0.31),
        "water_cut": _well(water_cut=91),
        "submergence": _well(hdin=850),  # 50 м
        "mrp": _well(mrp_days=None),
        "telemetry": _well(telemetry=replace(TEL_SHGN, k=0.2)),  # K > P70
    }
    for key, item in cases.items():
        result = evaluate(item, NORMS)
        assert not result.in_pool, key
        assert key in {k for k, _text in result.fails}, key


def test_evn_rules() -> None:
    evn = _well(name="VMB_0001", field="VMB", lift=LIFT_EVN, oil=2.1, telemetry=TEL_EVN)

    assert evaluate(evn, NORMS).in_pool  # ЭВН: порог нефти 2,0
    for tel in (
        replace(TEL_EVN, moment_cv=0.07),
        replace(TEL_EVN, moment_drift=0.09),
        replace(TEL_EVN, fill_p5=85),  # ЭВН: заполнение ≥ 90 %
    ):
        assert not evaluate(replace(evn, telemetry=tel), NORMS).in_pool


def test_without_telemetry_passes_but_not_in_pool() -> None:
    result = evaluate(_well(telemetry=None), NORMS)

    assert result.passed
    assert not result.has_telemetry
    assert not result.in_pool
    assert result.step_percent == 15


def test_flags_do_not_exclude() -> None:
    result = evaluate(
        _well(
            cv=0.27,
            water_cut=86,
            since_repair_days=10,
            pump_age_days=900,
            flat=True,
        ),
        NORMS,
    )

    assert result.in_pool
    assert len(result.flags) == 5


def test_field_norms_percentiles() -> None:
    items = [
        _well(well_id=i, telemetry=replace(TEL_SHGN, k=k), pump_age_days=a)
        for i, (k, a) in enumerate([(0.1, 100), (0.2, 200), (0.3, 300), (0.4, 400)])
    ]
    norms = field_norms(items)

    assert round(norms.k_max["UZK"], 3) == 0.31  # P70 из 0,1–0,4
    assert round(norms.pump_age_max["UZK"], 1) == 355.0  # P85


def test_funnel_counts_step_by_step() -> None:
    results = {
        1: evaluate(_well(well_id=1), NORMS),
        2: evaluate(_well(well_id=2, oil=1.0), NORMS),
        3: evaluate(_well(well_id=3, telemetry=None), NORMS),
    }
    steps = dict(funnel(results))

    assert steps["всего"] == 3
    assert steps["нефть ≥ 2 / 2,5 т/сут"] == 2
    assert steps["в пуле (с телеметрией)"] == 1


def test_uncomputed_telemetry_metric_fails() -> None:
    dead_k = _well(telemetry=replace(TEL_SHGN, k=None))
    dead_evn = _well(
        field="VMB",
        lift=LIFT_EVN,
        telemetry=replace(TEL_EVN, moment_cv=None),
    )

    assert "K не посчитан" in [text for _k, text in evaluate(dead_k, NORMS).fails]
    assert not evaluate(dead_evn, NORMS).in_pool
