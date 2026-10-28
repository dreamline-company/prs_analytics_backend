"""Автомат привода: смена по двум отсчётам, перерывы, порядок отсчётов."""

# Времена в тестах — местные наивные, как в СДМО и ABAI.
# ruff: noqa: DTZ001

from datetime import datetime, timedelta

from apps.telemetry.services.drive import drive_running, vlt_running
from apps.wells.services.well_state.drive import (
    DriveChange,
    DriveSample,
    DriveSnapshot,
    DriveStats,
    HistoryScan,
    advance,
)

T0 = datetime(2026, 10, 19, 16, 0)
STEP = timedelta(minutes=5)


def _samples(
    *values: tuple[float | None, float | None],
    start: datetime = T0,
) -> list[DriveSample]:
    return [
        DriveSample(start + STEP * i, vlt, rpm) for i, (vlt, rpm) in enumerate(values)
    ]


def _running(since: datetime) -> DriveSnapshot:
    return DriveSnapshot(running=True, since=since, last_sample_at=since)


def test_vlt_codes() -> None:
    assert vlt_running(1.0) is True
    assert vlt_running(32) is True
    assert vlt_running(0) is False
    assert vlt_running(8192) is False
    # REAL-значение float32 большого кода остановки
    assert vlt_running(134250496.0) is False
    assert vlt_running(None) is None
    assert vlt_running(100) is None  # неизвестный код


def test_drive_running_falls_back_to_rpm() -> None:
    assert drive_running(None, 140) is True
    assert drive_running(None, 0) is False
    assert drive_running(100, 0) is False  # неизвестный код — по оборотам
    assert drive_running(1, 0) is True  # статус главнее оборотов
    assert drive_running(None, None) is None


def test_first_confirmation_from_unknown() -> None:
    snapshot = DriveSnapshot()
    changes = advance(snapshot, _samples((1, 140), (1, 140)))
    assert changes == [DriveChange(running=True, at=T0)]
    assert snapshot.running is True
    assert snapshot.since == T0


def test_single_zero_sample_does_not_change_state() -> None:
    snapshot = _running(T0 - STEP)
    changes = advance(snapshot, _samples((0, 0), (1, 140), (1, 140)))
    assert changes == []
    assert snapshot.running is True
    assert snapshot.since == T0 - STEP
    assert snapshot.pending_running is None


def test_two_zero_samples_stop_from_first() -> None:
    snapshot = _running(T0 - STEP)
    changes = advance(snapshot, _samples((0, 0), (0, 0)))
    assert changes == [DriveChange(running=False, at=T0)]
    assert snapshot.since == T0


def test_pending_survives_between_runs() -> None:
    snapshot = _running(T0 - STEP)
    assert advance(snapshot, _samples((0, 0))) == []
    assert snapshot.pending_running is False
    changes = advance(snapshot, _samples((0, 0), start=T0 + STEP))
    assert changes == [DriveChange(running=False, at=T0)]


def test_two_stops_in_one_chunk() -> None:
    snapshot = _running(T0 - STEP)
    changes = advance(
        snapshot,
        _samples((0, 0), (0, 0), (1, 140), (1, 140), (0, 0), (0, 0)),
    )
    assert [change.running for change in changes] == [False, True, False]
    assert [change.at for change in changes] == [T0, T0 + 2 * STEP, T0 + 4 * STEP]


def test_out_of_order_sample_is_skipped() -> None:
    snapshot = _running(T0)
    stats = DriveStats()
    changes = advance(
        snapshot,
        [DriveSample(T0 - STEP, 0, 0), DriveSample(T0 - 2 * STEP, 0, 0)],
        stats,
    )
    assert changes == []
    assert stats.out_of_order == 2
    assert snapshot.running is True


def test_unusable_sample_updates_last_seen_only() -> None:
    snapshot = _running(T0)
    advance(snapshot, [DriveSample(T0 + STEP, None, None)])
    assert snapshot.last_seen_at == T0 + STEP
    assert snapshot.last_sample_at == T0


def test_gap_same_state_keeps_since() -> None:
    snapshot = _running(T0)
    after_gap = T0 + timedelta(hours=3)
    advance(snapshot, _samples((1, 140), start=after_gap))
    # Первый отсчёт после перерыва совпал с прежним — подтверждено сразу.
    assert snapshot.reconfirming is False
    assert snapshot.since == T0
    assert snapshot.is_known(after_gap)


def test_gap_other_state_unknown_until_confirmed() -> None:
    snapshot = _running(T0)
    after_gap = T0 + timedelta(hours=3)
    advance(snapshot, _samples((0, 0), start=after_gap))
    assert snapshot.reconfirming is True
    assert not snapshot.is_known(after_gap)


def test_gap_clears_pending_of_unknown_state() -> None:
    # Два отсчёта с разницей в сутки не подтверждают состояние.
    snapshot = DriveSnapshot()
    advance(snapshot, _samples((1, 140)))
    changes = advance(snapshot, _samples((1, 140), start=T0 + timedelta(days=1)))
    assert changes == []
    assert snapshot.running is None
    assert snapshot.pending_since == T0 + timedelta(days=1)


def test_gap_other_state_starts_after_gap() -> None:
    snapshot = _running(T0)
    after_gap = T0 + timedelta(hours=3)
    changes = advance(snapshot, _samples((0, 0), (0, 0), start=after_gap))
    assert changes == [DriveChange(running=False, at=after_gap)]


def test_is_known_needs_fresh_sample() -> None:
    snapshot = _running(T0)
    assert snapshot.is_known(T0 + timedelta(minutes=60))
    assert not snapshot.is_known(T0 + timedelta(minutes=61))


def test_outage_freezes_only_state_fresh_at_outage_start() -> None:
    later = T0 + timedelta(hours=5)
    fresh = _running(T0)
    assert fresh.is_known(later, outage_since=T0 + timedelta(minutes=20))
    # Станция молчала сутки до сбоя — сбой её состояние не «оживляет».
    stale = _running(T0 - timedelta(days=1))
    assert not stale.is_known(later, outage_since=T0 + timedelta(minutes=20))


def test_nan_and_inf_registers_are_unusable() -> None:
    assert vlt_running(float("nan")) is None
    assert drive_running(float("inf"), None) is None
    assert drive_running(None, float("nan")) is None
    assert drive_running(float("nan"), 140) is True


def _history(*values: tuple[float | None, float | None]) -> list[DriveSample]:
    """Отсчёты от новых к старым: values[0] — самый новый."""
    newest = T0 + STEP * (len(values) - 1)
    return [
        DriveSample(newest - STEP * i, vlt, rpm) for i, (vlt, rpm) in enumerate(values)
    ]


def test_history_scan_finds_start_of_current_state() -> None:
    # Новые → старые: работает 3 отсчёта, раньше стоял (с одним сбойным нулём
    # внутри работы — не граница).
    samples = _history((1, 140), (0, 0), (1, 140), (1, 140), (0, 0), (0, 0), (0, 0))
    scan = HistoryScan()
    assert scan.feed(samples[:3]) is False
    assert scan.feed(samples[3:]) is True
    snapshot = scan.snapshot()
    assert snapshot.running is True
    assert snapshot.since == samples[3].savetime
    assert snapshot.last_seen_at == samples[0].savetime


def test_history_scan_without_change_clamps_to_oldest() -> None:
    samples = _history((1, 140), (1, 140), (1, 140), (1, 140))
    scan = HistoryScan()
    assert scan.feed(samples) is False
    snapshot = scan.snapshot()
    assert snapshot.running is True
    assert snapshot.since == samples[-1].savetime


def test_history_scan_keeps_pending_candidate() -> None:
    # Самый новый отсчёт — первый ноль после работы: кандидат, не смена.
    samples = _history((0, 0), (1, 140), (1, 140), (0, 0), (0, 0))
    scan = HistoryScan()
    scan.feed(samples)
    snapshot = scan.snapshot()
    assert snapshot.running is True
    assert snapshot.pending_running is False
    assert snapshot.pending_since == samples[0].savetime


def test_history_scan_without_usable_samples() -> None:
    scan = HistoryScan()
    assert scan.feed(_history((None, None), (None, None))) is False
    assert scan.target is None
    assert scan.snapshot().running is None
