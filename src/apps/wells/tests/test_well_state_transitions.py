"""Переходы журнала состояний: когда новая строка, когда на месте."""

# Времена в тестах — местные наивные, как в СДМО и ABAI.
# ruff: noqa: DTZ001

from datetime import datetime

from apps.wells.models.well_state import (
    SOURCE_ABAI,
    SOURCE_DETECTOR,
    SOURCE_REPAIR,
    SOURCE_SDMO,
    STATE_ALARM,
    STATE_REPAIR,
    STATE_STOPPED,
    STATE_WARNING,
    STATE_WORKING,
)
from apps.wells.services.well_state.rules import Decision
from apps.wells.services.well_state.transitions import (
    KIND_NONE,
    KIND_OPEN,
    KIND_REPLACE,
    KIND_REWRITE,
    KIND_UPDATE,
    OpenState,
    plan_transition,
)

NOW = datetime(2026, 10, 31, 15, 30)


def _decision(
    state: str,
    started: datetime | None,
    *,
    source: str = SOURCE_SDMO,
    code: str = "x",
    text: str = "x",
) -> Decision:
    return Decision(state, source, code, text, started, {})


def _open(
    state: str,
    started: datetime,
    *,
    source: str = SOURCE_SDMO,
    code: str = "x",
    text: str = "x",
) -> OpenState:
    return OpenState(1, state, source, code, text, started)


def test_first_row_opens_at_data_start() -> None:
    start = datetime(2026, 10, 19, 16, 19, 19)
    transition = plan_transition(None, _decision(STATE_STOPPED, start), NOW)
    assert transition.kind == KIND_OPEN
    assert transition.started_at == start


def test_unknown_start_opens_now() -> None:
    transition = plan_transition(None, _decision(STATE_WORKING, None), NOW)
    assert transition.started_at == NOW


def test_same_state_same_reason_does_nothing() -> None:
    open_state = _open(STATE_WORKING, datetime(2026, 10, 27))
    transition = plan_transition(
        open_state,
        _decision(STATE_WORKING, datetime(2026, 10, 27)),
        NOW,
    )
    assert transition.kind == KIND_NONE


def test_same_state_new_detail_text_updates_in_place() -> None:
    # Строки, записанные до появления reason_detail_ru, переписываются на месте
    # первым же прогоном — без новой строки в журнале.
    started = datetime(2026, 10, 27)
    open_state = _open(STATE_WORKING, started, text="Привод работает")
    decision = Decision(
        STATE_WORKING,
        SOURCE_SDMO,
        "x",
        "Работает в штатном режиме",
        started,
        {},
        "Привод работает",
    )
    assert plan_transition(open_state, decision, NOW).kind == KIND_UPDATE


def test_reason_change_updates_in_place() -> None:
    # SDMO-стоп в 10:00, ABAI внёс простой в 15:10 — причина дополняется.
    open_state = _open(
        STATE_STOPPED,
        datetime(2026, 10, 19, 10, 0),
        text="Привод остановлен (СДМО)",
    )
    decision = _decision(
        STATE_STOPPED,
        datetime(2026, 10, 19, 10, 0),
        text="Привод остановлен (СДМО), в ABAI «Нет подачи»",
    )
    assert plan_transition(open_state, decision, NOW).kind == KIND_UPDATE


def test_later_data_start_replaces() -> None:
    open_state = _open(STATE_WORKING, datetime(2026, 10, 27, 8, 15))
    stop = datetime(2026, 10, 30, 14, 2, 10)
    transition = plan_transition(open_state, _decision(STATE_STOPPED, stop), NOW)
    assert transition.kind == KIND_REPLACE
    assert transition.started_at == stop


def test_escalation_uses_its_own_start() -> None:
    open_state = _open(
        STATE_WARNING,
        datetime(2026, 10, 31, 6, 0),
        source=SOURCE_DETECTOR,
    )
    escalated = datetime(2026, 10, 31, 7, 5)
    transition = plan_transition(
        open_state,
        _decision(STATE_ALARM, escalated, source=SOURCE_DETECTOR),
        NOW,
    )
    assert transition.kind == KIND_REPLACE
    assert transition.started_at == escalated


def test_abai_stop_renamed_to_repair_in_place() -> None:
    # Ремонт пришёл утром, в ABAI начат на 5 минут раньше статуса.
    open_state = _open(
        STATE_STOPPED,
        datetime(2026, 10, 31, 15, 20),
        source=SOURCE_ABAI,
    )
    repair = _decision(
        STATE_REPAIR,
        datetime(2026, 10, 31, 15, 15),
        source=SOURCE_REPAIR,
    )
    transition = plan_transition(open_state, repair, datetime(2026, 11, 1, 5, 0))
    assert transition.kind == KIND_REWRITE
    assert transition.started_at == datetime(2026, 10, 31, 15, 20)


def test_equal_start_rewrites_in_place() -> None:
    start = datetime(2026, 10, 31, 15, 20)
    open_state = _open(STATE_STOPPED, start, source=SOURCE_SDMO)
    transition = plan_transition(open_state, _decision(STATE_REPAIR, start), NOW)
    assert transition.kind == KIND_REWRITE


def test_repair_warning_repair_keeps_warning_period() -> None:
    # Ремонт с 10:00, привод запустили в 12:00 (warning), встал в 13:00 —
    # снова ремонт; период warning не должен переписаться.
    open_state = _open(
        STATE_WARNING,
        datetime(2026, 10, 31, 12, 0),
        source=SOURCE_REPAIR,
    )
    seen = datetime(2026, 10, 31, 13, 0)
    repair = _decision(
        STATE_REPAIR,
        datetime(2026, 10, 31, 10, 0),
        source=SOURCE_REPAIR,
    )
    transition = plan_transition(open_state, repair, seen)
    assert transition.kind == KIND_REPLACE
    assert transition.started_at == seen


def test_future_start_is_clamped_to_now() -> None:
    transition = plan_transition(
        None,
        _decision(STATE_WORKING, datetime(2026, 11, 2)),
        NOW,
    )
    assert transition.started_at == NOW


def test_sdmo_stop_renamed_to_repair_with_minute_rounded_start() -> None:
    # VMB_2058: привод встал в 16:19:19, ABAI ставит ремонт с 16:19:00.
    stop = datetime(2026, 10, 19, 16, 19, 19)
    open_state = _open(STATE_STOPPED, stop, source=SOURCE_SDMO)
    repair = _decision(
        STATE_REPAIR,
        datetime(2026, 10, 19, 16, 19),
        source=SOURCE_REPAIR,
    )
    transition = plan_transition(open_state, repair, datetime(2026, 10, 20, 5, 5))
    assert transition.kind == KIND_REWRITE
    assert transition.started_at == stop
