"""Тесты проверки эпизода R2 независимыми данными (verification.rule)."""

from datetime import datetime, timedelta

from apps.detectors.models.verification import (
    REASON_ABAI_STATUS,
    REASON_DRIVE_STOPPED,
    REASON_DRIVE_UNSTABLE,
    REASON_NO_MEASUREMENT,
    REASON_NO_STATUS,
    REASON_OIL_LOW,
    REASON_OIL_NOT_MEASURED,
    REASON_OIL_OK_DRIVE_OK,
    REASON_REPAIR,
    REASON_REPAIR_ROD_BREAK,
    VERDICT_FAILURE_CONFIRMED,
    VERDICT_FAILURE_LIKELY,
    VERDICT_FALSE_ALARM,
    VERDICT_PENDING,
    VERDICT_UNDETERMINED,
)
from apps.detectors.rod_breaks.verification import config
from apps.detectors.rod_breaks.verification.rule import (
    AbaiStatusEvent,
    Measurement,
    RepairEvent,
    StatusSample,
    VerificationInput,
    evaluate,
    is_abai_failure,
    oil_norm,
)

# Местное время, как opened_at эпизода и savetime СДМО.
T0 = datetime(2026, 9, 28, 10, 0)  # noqa: DTZ001
H = timedelta(hours=1)
WORK = 1
STOP = 0


def _status(
    code_at: dict[float, int],
    until_h: float = 110,
    step_min: int = 2,
) -> list[StatusSample]:
    """Отсчёты статуса каждые ``step_min`` минут от T0 до ``until_h``.

    ``code_at`` — {час от T0: код с этого часа}; по умолчанию «работает».
    """
    marks = sorted(code_at.items())
    samples = []
    t = T0
    while t < T0 + until_h * H:
        hours = (t - T0) / H
        code = WORK
        for start, value in marks:
            if hours >= start:
                code = value
        samples.append(StatusSample(at=t, code=code))
        t += timedelta(minutes=step_min)
    return samples


def _input(
    *,
    status: list[StatusSample] | None = None,
    measurements: list[Measurement] | None = None,
    tech_regime_oil: float | None = 6.0,
    repairs: list[RepairEvent] | None = None,
    abai: list[AbaiStatusEvent] | None = None,
) -> VerificationInput:
    return VerificationInput(
        t0=T0,
        status_samples=status if status is not None else _status({}),
        measurements=measurements or [],
        tech_regime_oil=tech_regime_oil,
        repairs=repairs or [],
        abai_statuses=abai or [],
    )


def _oil(hours: float, oil: float | None, liquid: float | None = 20.0) -> Measurement:
    return Measurement(at=T0 + hours * H, qm_oil=oil, qv_liquid=liquid)


# --- Ложная тревога ---


def test_false_alarm_when_oil_ok_and_drive_ran_12h_after() -> None:
    inp = _input(measurements=[_oil(10, 5.0)])
    result = evaluate(inp, now=T0 + 23 * H)
    assert result.verdict == VERDICT_FALSE_ALARM
    assert result.reason == REASON_OIL_OK_DRIVE_OK
    assert result.evidence_at == T0 + 22 * H
    assert result.evidence["oil"]["share_of_norm"] == round(5.0 / 6.0, 3)
    assert result.is_final is False


def test_pending_until_drive_ran_12h_after_measurement() -> None:
    inp = _input(measurements=[_oil(10, 5.0)])
    result = evaluate(inp, now=T0 + 15 * H)
    assert result.verdict == VERDICT_PENDING
    assert result.evidence["waiting"] == "drive_after_measurement"


def test_measurement_too_early_is_ignored() -> None:
    # Замер в первые 4 часа описывает время до отказа.
    inp = _input(measurements=[_oil(2, 6.0)])
    assert evaluate(inp, now=T0 + 30 * H).verdict == VERDICT_PENDING


def test_low_oil_does_not_prove_normal_work() -> None:
    # 1 т при норме 6 т — после обрыва нефть падает, но не всегда до нуля.
    inp = _input(measurements=[_oil(10, 1.0)])
    result = evaluate(inp, now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_OIL_LOW


def test_garbage_measurement_oil_above_liquid_is_ignored() -> None:
    inp = _input(measurements=[_oil(10, 30.0, liquid=10.0)])
    result = evaluate(inp, now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_OIL_NOT_MEASURED


def test_drive_stopped_right_after_measurement_is_not_false_alarm() -> None:
    # Фантомный замер: нефть есть, а через час оператор выключил привод.
    inp = _input(
        status=_status({11: STOP}),
        measurements=[_oil(10, 6.0)],
    )
    result = evaluate(inp, now=T0 + 40 * H)
    assert result.verdict == VERDICT_FAILURE_LIKELY
    assert result.reason == REASON_DRIVE_STOPPED


def test_norm_is_max_of_tech_regime_and_median_before() -> None:
    before = [_oil(-24 * d, 3.0) for d in (1, 3, 5)]
    assert oil_norm(_input(measurements=before, tech_regime_oil=6.0)) == (
        6.0,
        "tech_regime",
    )
    assert oil_norm(_input(measurements=before, tech_regime_oil=2.0)) == (
        3.0,
        "median_14d",
    )
    assert oil_norm(_input(measurements=[], tech_regime_oil=None)) == (None, None)


# --- Подтверждение и «скорее всего» ---


def test_repair_with_rod_break_confirms_and_is_final() -> None:
    inp = _input(
        measurements=[_oil(10, 6.0)],
        repairs=[
            RepairEvent(1, T0 + 30 * H, rod_break=True, work_list="Обрыв 18-ой штанги"),
        ],
    )
    result = evaluate(inp, now=T0 + 31 * H)
    assert result.verdict == VERDICT_FAILURE_CONFIRMED
    assert result.reason == REASON_REPAIR_ROD_BREAK
    assert result.is_final is True
    assert result.evidence_at == T0 + 30 * H


def test_repair_overrides_earlier_false_alarm() -> None:
    inp = _input(
        measurements=[_oil(10, 6.0)],
        repairs=[RepairEvent(1, T0 + 5 * 24 * H, rod_break=False, work_list="ПРС")],
    )
    assert evaluate(inp, now=T0 + 30 * H).verdict == VERDICT_FALSE_ALARM
    later = evaluate(inp, now=T0 + 6 * 24 * H)
    assert later.verdict == VERDICT_FAILURE_CONFIRMED
    assert later.reason == REASON_REPAIR


def test_repair_before_lookback_does_not_confirm() -> None:
    inp = _input(
        repairs=[RepairEvent(1, T0 - 30 * H, rod_break=True, work_list="обрыв")],
    )
    assert evaluate(inp, now=T0 + 5 * H).verdict == VERDICT_PENDING


def test_abai_no_supply_confirms() -> None:
    inp = _input(abai=[AbaiStatusEvent(T0 + 20 * H, None, "DWN", "Нет подачи")])
    result = evaluate(inp, now=T0 + 21 * H)
    assert result.verdict == VERDICT_FAILURE_CONFIRMED
    assert result.reason == REASON_ABAI_STATUS


def test_abai_status_already_covering_t0_confirms() -> None:
    inp = _input(abai=[AbaiStatusEvent(T0 - 72 * H, None, "DWN", "Нет подачи")])
    assert evaluate(inp, now=T0 + 1 * H).verdict == VERDICT_FAILURE_CONFIRMED


def test_power_outage_is_not_failure_but_cuts_measurement_window() -> None:
    outage = AbaiStatusEvent(
        T0 + 8 * H,
        T0 + 9 * H,
        "DWN",
        "Плановое отключение электроэнергии",
    )
    assert not is_abai_failure(outage)
    # Замер после отключения уже не про эту тревогу.
    inp = _input(measurements=[_oil(10, 6.0)], abai=[outage])
    result = evaluate(inp, now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_NO_MEASUREMENT


def test_failure_likely_after_10_stopped_hours() -> None:
    inp = _input(status=_status({20: STOP}))
    assert evaluate(inp, now=T0 + 25 * H).verdict == VERDICT_PENDING
    result = evaluate(inp, now=T0 + 31 * H)
    assert result.verdict == VERDICT_FAILURE_LIKELY
    assert result.evidence_at == T0 + 30 * H


def test_false_alarm_established_earlier_wins_over_later_stop() -> None:
    # Ложная доказана в T0+22 ч, долгая остановка (отключение света) — позже.
    inp = _input(status=_status({40: STOP}), measurements=[_oil(10, 5.0)])
    result = evaluate(inp, now=T0 + 60 * H)
    assert result.verdict == VERDICT_FALSE_ALARM


# --- Не удалось проверить ---


def test_undetermined_no_measurement_after_window() -> None:
    result = evaluate(_input(), now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_NO_MEASUREMENT


def test_undetermined_no_status() -> None:
    result = evaluate(_input(status=[], measurements=[_oil(10, 6.0)]), now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_NO_STATUS


def test_undetermined_oil_not_measured() -> None:
    inp = _input(measurements=[_oil(10, None), _oil(30, 0.0)])
    result = evaluate(inp, now=T0 + 97 * H)
    assert result.reason == REASON_OIL_NOT_MEASURED


def test_undetermined_drive_unstable() -> None:
    # Нефть в норме, но привод работал меньше 90 % (останавливали 3 часа).
    inp = _input(
        status=_status({2: STOP, 5: WORK}),
        measurements=[_oil(10, 6.0)],
    )
    result = evaluate(inp, now=T0 + 97 * H)
    assert result.verdict == VERDICT_UNDETERMINED
    assert result.reason == REASON_DRIVE_UNSTABLE


def test_waits_past_window_for_drive_after_late_measurement() -> None:
    # Замер на 95-м часу: решение не раньше 107-го, «не удалось» не ставим.
    inp = _input(measurements=[_oil(95, 6.0)])
    assert evaluate(inp, now=T0 + 100 * H).verdict == VERDICT_PENDING
    assert evaluate(inp, now=T0 + 108 * H).verdict == VERDICT_FALSE_ALARM


def test_final_after_seven_days() -> None:
    inp = _input(measurements=[_oil(10, 5.0)])
    result = evaluate(inp, now=T0 + timedelta(days=config.FINAL_DAYS))
    assert result.verdict == VERDICT_FALSE_ALARM
    assert result.is_final is True
