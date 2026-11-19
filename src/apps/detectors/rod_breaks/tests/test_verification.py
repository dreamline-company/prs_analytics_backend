"""Тесты проверки эпизода R2 (verification.rule, правило r2-verify-v3)."""

from datetime import datetime, timedelta

from apps.detectors.models.verification import (
    REASON_DURING_REPAIR,
    REASON_MOMENT_RECOVERED,
    REASON_NO_ROD_BREAK_REPAIR,
    REASON_OIL_OK_DRIVE_OK,
    REASON_REPAIR_OTHER,
    REASON_REPAIR_ROD_BREAK,
    VERDICT_CONFIRMED,
    VERDICT_FALSE_ALARM,
    VERDICT_NOT_CONFIRMED,
    VERDICT_PENDING,
    is_false_signal,
)
from apps.detectors.rod_breaks.verification import config
from apps.detectors.rod_breaks.verification.rule import (
    AbaiStatusEvent,
    Measurement,
    MomentSample,
    RepairEvent,
    StatusSample,
    VerificationInput,
    evaluate,
    is_abai_failure,
    mentions_rod_break,
    oil_norm,
)

# Местное время, как opened_at эпизода и savetime СДМО.
T0 = datetime(2026, 9, 28, 10, 0)  # noqa: DTZ001
H = timedelta(hours=1)
MINUTE = timedelta(minutes=1)
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


def _moments(
    base: float,
    after: float,
    *,
    base_speed: float = 60.0,
    after_until_h: float = 6,
    step_min: int = 2,
) -> list[MomentSample]:
    """Момент каждые ``step_min`` минут: ``base`` за сутки до T0, ``after`` после."""
    samples = []
    t = T0 - 24 * H
    while t < T0 + after_until_h * H:
        before_t0 = t < T0
        samples.append(
            MomentSample(
                at=t,
                moment=base if before_t0 else after,
                speed=base_speed if before_t0 else 60.0,
            ),
        )
        t += timedelta(minutes=step_min)
    return samples


def _input(  # noqa: PLR0913
    *,
    status: list[StatusSample] | None = None,
    measurements: list[Measurement] | None = None,
    tech_regime_oil: float | None = 6.0,
    repairs: list[RepairEvent] | None = None,
    abai: list[AbaiStatusEvent] | None = None,
    moments: list[MomentSample] | None = None,
) -> VerificationInput:
    return VerificationInput(
        t0=T0,
        status_samples=status if status is not None else _status({}),
        measurements=measurements or [],
        tech_regime_oil=tech_regime_oil,
        repairs=repairs or [],
        abai_statuses=abai or [],
        moment_samples=moments or [],
    )


def _oil(hours: float, oil: float | None, liquid: float | None = 20.0) -> Measurement:
    return Measurement(at=T0 + hours * H, qm_oil=oil, qv_liquid=liquid)


DAY = 24 * H
PRS = config.ABAI_REPAIR_TYPE_PRS
KRS = 1
WAIT = config.PRS_WAIT_DAYS * DAY


def _repair(  # noqa: PLR0913
    start_h: float,
    work_list: str = "Обрыв 18-ой штанги 22мм. Замена штанги.",
    *,
    end_h: float | None = 30,
    type_id: int | None = PRS,
    type_name: str = "TP 4-2 Устранение обрыва штанг",
    repair_id: int = 1,
) -> RepairEvent:
    return RepairEvent(
        repair_id=repair_id,
        start=T0 + start_h * H,
        end=T0 + (start_h + end_h) * H if end_h is not None else None,
        work_list=work_list,
        abai_type_id=type_id,
        type_name=type_name,
    )


OTHER_WORKS = "Нет подачи. Подъем ПО. Смена насоса. Пуск."
OTHER_TYPE = "TP 4-1 Ревизия и смена насоса"


# --- Обрыв штанг в тексте работ ---


def test_rod_break_phrases_from_prod() -> None:
    for text in (
        "обрыв 34-ой штанги 22мм",
        "нп;  ↑по; обрыв 61-штанги.19мм д. з/з-м",
        "обрыв короткий штаги (3метр) 19мм",
        "При подъеме штанг обнаружен слом на 17-ой штанге.Пропарка.",
        "Срыв резьбы 16-ой штанги 19мм. Извлечен глубинный манометр.",
        "1шт НКТ, Слом штока. замена полированного штока.",
        "обрыв 8-ой центратора   22мм дсупер",
        "Устранение обрыва штанги",
        "отворот 49-ой штанги полый 34мм",
    ):
        assert mentions_rod_break(text), text


def test_not_rod_break_phrases_from_prod() -> None:
    for text in (
        "нп;  ↑ПО; Отворот 1 штук НКТ;  з/з-530м",
        "нп;  ↑по; обрыв кабеля 43-й нкт с",
        "отворот плунжера.смена насоса.пропарка нкт-43шт.",
        "сухарь якоря сломан. Замена. Штанги цельные.",
        "обрыв НКТ, подъём штанг",
        "Заклин насоса. Подъем ПО. Смена насоса.",
        "",
    ):
        assert not mentions_rod_break(text), text


def test_prs_by_category_or_work_type() -> None:
    assert _repair(5).is_prs
    assert _repair(5, type_id=None, type_name="TP 4-2 Устранение обрыва штанг").is_prs
    assert not _repair(5, type_id=KRS, type_name="КР-6 Ловильные работы").is_prs


# --- Подтверждено ---


def test_prs_with_rod_break_confirms_and_is_final() -> None:
    inp = _input(measurements=[_oil(10, 6.0)], repairs=[_repair(30)])
    result = evaluate(inp, now=T0 + 31 * H)
    assert result.verdict == VERDICT_CONFIRMED
    assert result.reason == REASON_REPAIR_ROD_BREAK
    assert result.is_final is True
    assert result.evidence_at == T0 + 30 * H
    assert result.evidence["repair"][0]["rod_break"] is True


def test_late_prs_within_window_confirms() -> None:
    # VMB_2017: ПРС с обрывом через 31 сутки.
    inp = _input(repairs=[_repair(31 * 24)])
    assert evaluate(inp, now=T0 + 32 * DAY).verdict == VERDICT_CONFIRMED


def test_prs_after_window_does_not_confirm() -> None:
    inp = _input(repairs=[_repair(config.PRS_WAIT_DAYS * 24 + 24)])
    result = evaluate(inp, now=T0 + WAIT + 2 * DAY)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_NO_ROD_BREAK_REPAIR


def test_rod_break_by_work_type_when_text_empty_after_closing() -> None:
    open_repair = _repair(10, "", end_h=None)
    assert evaluate(_input(repairs=[open_repair]), now=T0 + 20 * H).verdict == (
        VERDICT_PENDING
    )
    closed = _repair(10, "", end_h=20)
    assert evaluate(_input(repairs=[closed]), now=T0 + 40 * H).verdict == (
        VERDICT_CONFIRMED
    )


def test_text_decides_over_work_type() -> None:
    # Вид TP 4-2, а в работах «отворот НКТ» — не обрыв штанг.
    repair = _repair(10, "нп; ↑ПО; Отворот 1 штук НКТ; спуск", end_h=20)
    result = evaluate(_input(repairs=[repair]), now=T0 + 4 * DAY)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_REPAIR_OTHER


def test_krs_with_rod_break_does_not_confirm() -> None:
    repair = _repair(10, type_id=KRS, type_name="КР-6 Ловильные работы", end_h=20)
    result = evaluate(_input(repairs=[repair]), now=T0 + 4 * DAY)
    assert result.reason == REASON_REPAIR_OTHER


def test_rod_break_prs_already_running_at_t0_confirms() -> None:
    inp = _input(repairs=[_repair(-5, end_h=None)])
    assert evaluate(inp, now=T0 + 1 * H).verdict == VERDICT_CONFIRMED


# --- Не подтверждено: ремонт ---


def test_other_repair_waits_for_closing_plus_grace() -> None:
    repair = _repair(20, OTHER_WORKS, type_name=OTHER_TYPE, end_h=24)
    inp = _input(repairs=[repair])
    running = evaluate(inp, now=T0 + 30 * H)
    assert running.verdict == VERDICT_PENDING
    assert running.evidence["waiting"] == "repair_works"
    done = evaluate(inp, now=T0 + (20 + 24) * H + config.REPAIR_TEXT_GRACE_DAYS * DAY)
    assert done.verdict == VERDICT_NOT_CONFIRMED
    assert done.reason == REASON_REPAIR_OTHER
    assert done.is_final is True


def test_later_rod_break_prs_does_not_confirm_after_other_repair() -> None:
    # VMB_0418 #681: первый ремонт — другого вида, ПРС с обрывом — позже.
    inp = _input(
        repairs=[
            _repair(37, OTHER_WORKS, type_name=OTHER_TYPE, end_h=20),
            _repair(400, repair_id=2),
        ],
    )
    assert evaluate(inp, now=T0 + 20 * DAY).reason == REASON_REPAIR_OTHER


def test_signal_inside_running_repair_is_during_repair() -> None:
    # VMB_1148: ремонт начат за 8 ч до тревоги и ещё идёт.
    repair = _repair(-8, OTHER_WORKS, type_name=OTHER_TYPE, end_h=None)
    result = evaluate(_input(repairs=[repair]), now=T0 + 2 * H)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_DURING_REPAIR


def test_repair_finished_before_t0_is_ignored() -> None:
    repair = _repair(-20, end_h=10)
    assert evaluate(_input(repairs=[repair]), now=T0 + 5 * H).verdict == (
        VERDICT_PENDING
    )


def test_abai_no_supply_alone_does_not_confirm() -> None:
    inp = _input(abai=[AbaiStatusEvent(T0 + 20 * H, None, "DWN", "Нет подачи")])
    result = evaluate(inp, now=T0 + 21 * H)
    assert result.verdict == VERDICT_PENDING
    assert result.evidence["abai"][0]["reason"] == "Нет подачи"
    late = evaluate(inp, now=T0 + WAIT)
    assert late.verdict == VERDICT_NOT_CONFIRMED
    assert late.reason == REASON_NO_ROD_BREAK_REPAIR
    assert late.is_final is True


# --- Не подтверждено: момент и нефть ---


def test_not_confirmed_when_moment_recovered_within_6h() -> None:
    inp = _input(moments=_moments(base=100.0, after=90.0))
    result = evaluate(inp, now=T0 + 7 * H)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_MOMENT_RECOVERED
    assert result.evidence_at == T0 + 6 * H
    assert result.is_final is True
    moment = result.evidence["moment"]
    assert (moment["base"], moment["level"], moment["share_of_base"]) == (
        100.0,
        90.0,
        0.9,
    )


def test_moment_at_threshold_counts_as_recovered() -> None:
    inp = _input(moments=_moments(base=100.0, after=75.0))
    assert evaluate(inp, now=T0 + 7 * H).reason == REASON_MOMENT_RECOVERED


def test_moment_not_decided_before_window_closes() -> None:
    inp = _input(moments=_moments(base=100.0, after=95.0, after_until_h=5))
    result = evaluate(inp, now=T0 + 5 * H)
    assert result.verdict == VERDICT_PENDING
    assert "moment" not in result.evidence


def test_moment_stayed_low_waits_for_prs() -> None:
    # Обрыв: вал крутится без нагрузки, момент 40 % обычного.
    inp = _input(moments=_moments(base=100.0, after=40.0))
    result = evaluate(inp, now=T0 + 7 * H)
    assert result.verdict == VERDICT_PENDING
    assert result.evidence["waiting"] == "prs"
    assert result.evidence["moment"]["share_of_base"] == 0.4


def test_weak_well_moment_is_not_trusted() -> None:
    # База 40 < 50: на слабых скважинах и сам порог R2 ненадёжен.
    inp = _input(moments=_moments(base=40.0, after=40.0))
    assert evaluate(inp, now=T0 + 7 * H).verdict == VERDICT_PENDING


def test_station_offline_after_alarm_moment_not_decided() -> None:
    # Связь пропала: в окне 1–6 ч отсчёты только первые 20 минут.
    samples = [s for s in _moments(base=100.0, after=100.0) if s.at < T0 + 80 * MINUTE]
    result = evaluate(_input(moments=samples), now=T0 + 7 * H)
    assert result.verdict == VERDICT_PENDING
    assert result.evidence["moment"]["samples"] < config.MOMENT_WINDOW_MIN_SAMPLES


def test_stopped_drive_does_not_lower_moment_base() -> None:
    # До T0 привод часть времени стоял (скорость 0, момент 0): база — по работе.
    samples = [
        MomentSample(at=s.at, moment=0.0, speed=0.0)
        if s.at < T0 and s.at.minute < 30
        else s
        for s in _moments(base=100.0, after=90.0)
    ]
    result = evaluate(_input(moments=samples), now=T0 + 7 * H)
    assert result.evidence["moment"]["base"] == 100.0
    assert result.reason == REASON_MOMENT_RECOVERED


def test_drive_stopped_after_alarm_is_not_recovered() -> None:
    # После тревоги привод выключили: момент 0 в окне — не «вернулся».
    samples = [
        MomentSample(at=s.at, moment=0.0, speed=0.0) if s.at >= T0 else s
        for s in _moments(base=100.0, after=100.0)
    ]
    result = evaluate(_input(moments=samples), now=T0 + 7 * H)
    assert result.evidence["moment"]["share_of_base"] == 0.0
    assert result.verdict == VERDICT_PENDING


def test_recovered_moment_wins_over_later_rod_break_prs() -> None:
    inp = _input(moments=_moments(base=100.0, after=95.0), repairs=[_repair(30)])
    result = evaluate(inp, now=T0 + 31 * H)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_MOMENT_RECOVERED


def test_rod_break_prs_inside_moment_window_wins() -> None:
    # ПРС начат в T0 + 3 ч — окно 1–6 ч уже про ремонт.
    inp = _input(moments=_moments(base=100.0, after=95.0), repairs=[_repair(3)])
    assert evaluate(inp, now=T0 + 7 * H).verdict == VERDICT_CONFIRMED


def test_not_confirmed_when_oil_ok_and_drive_ran_12h_after() -> None:
    inp = _input(measurements=[_oil(10, 5.0)])
    result = evaluate(inp, now=T0 + 23 * H)
    assert result.verdict == VERDICT_NOT_CONFIRMED
    assert result.reason == REASON_OIL_OK_DRIVE_OK
    assert result.evidence_at == T0 + 22 * H
    assert result.evidence["oil"]["share_of_norm"] == round(5.0 / 6.0, 3)
    assert result.is_final is True


def test_pending_until_drive_ran_12h_after_measurement() -> None:
    inp = _input(measurements=[_oil(10, 5.0)])
    assert evaluate(inp, now=T0 + 15 * H).verdict == VERDICT_PENDING


def test_measurement_too_early_is_ignored() -> None:
    # Замер в первые 4 часа описывает время до отказа.
    inp = _input(measurements=[_oil(2, 6.0)])
    assert evaluate(inp, now=T0 + 30 * H).verdict == VERDICT_PENDING


def test_low_oil_does_not_prove_normal_work() -> None:
    inp = _input(measurements=[_oil(10, 1.0)])
    assert evaluate(inp, now=T0 + 97 * H).verdict == VERDICT_PENDING


def test_garbage_measurement_oil_above_liquid_is_ignored() -> None:
    inp = _input(measurements=[_oil(10, 30.0, liquid=10.0)])
    assert evaluate(inp, now=T0 + 97 * H).verdict == VERDICT_PENDING


def test_drive_stopped_right_after_measurement_is_not_false_signal() -> None:
    # Фантомный замер: нефть есть, а через час оператор выключил привод.
    inp = _input(status=_status({11: STOP}), measurements=[_oil(10, 6.0)])
    result = evaluate(inp, now=T0 + 40 * H)
    assert result.verdict == VERDICT_PENDING
    stopped = result.evidence["drive"]["stopped_hours"]
    assert stopped >= config.DRIVE_STOPPED_HOURS_LIKELY


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
    assert evaluate(inp, now=T0 + 97 * H).verdict == VERDICT_PENDING


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


# --- Признак ложного сигнала для потребителей ---


def test_is_false_signal() -> None:
    assert is_false_signal(VERDICT_NOT_CONFIRMED, REASON_MOMENT_RECOVERED)
    assert is_false_signal(VERDICT_NOT_CONFIRMED, REASON_OIL_OK_DRIVE_OK)
    assert is_false_signal(VERDICT_NOT_CONFIRMED, REASON_DURING_REPAIR)
    assert is_false_signal(VERDICT_FALSE_ALARM, None)  # старая отметка
    assert not is_false_signal(VERDICT_NOT_CONFIRMED, REASON_REPAIR_OTHER)
    assert not is_false_signal(VERDICT_NOT_CONFIRMED, REASON_NO_ROD_BREAK_REPAIR)
    assert not is_false_signal(VERDICT_CONFIRMED, REASON_REPAIR_ROD_BREAK)
    assert not is_false_signal(None, None)


def test_open_repair_without_text_decided_by_work_type_after_window() -> None:
    # Висящий в ABAI ремонт без текста: после окна ожидания решает вид работ.
    repair = _repair(10, "", end_h=None)
    inp = _input(repairs=[repair])
    assert evaluate(inp, now=T0 + 10 * DAY).verdict == VERDICT_PENDING
    late = evaluate(inp, now=T0 + WAIT)
    assert late.verdict == VERDICT_CONFIRMED
    other = _input(repairs=[_repair(10, "", end_h=None, type_name=OTHER_TYPE)])
    assert evaluate(other, now=T0 + WAIT).reason == REASON_REPAIR_OTHER


def test_hanging_open_repair_does_not_swallow_later_rod_break_prs() -> None:
    # Ремонт начат за 10 сут до сигнала и не закрыт в ABAI — уже не «идёт».
    hanging = _repair(-240, OTHER_WORKS, type_name=OTHER_TYPE, end_h=None)
    inp = _input(repairs=[hanging, _repair(30, repair_id=2)])
    assert evaluate(inp, now=T0 + 31 * H).verdict == VERDICT_CONFIRMED
