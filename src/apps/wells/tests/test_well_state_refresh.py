"""Помощники прогона, чтение состояния и флаг устаревшего замера."""

# Времена в тестах — местные наивные, как в СДМО и ABAI.
# ruff: noqa: DTZ001

import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

from apps.detectors.dto.internal.well_status import (
    ActiveIncidentDTO,
    WellIncidentStatusDTO,
)
from apps.telemetry.services.well_rates import WellRatesService
from apps.wells.services.well_state.read import build_well_state
from apps.wells.services.well_state.refresh import (
    active_repairs,
    current_statuses,
    detect_outage,
    incident_input,
)

NOW = datetime(2026, 10, 28, 10, 40)
FAR = datetime(3333, 12, 31)


def _repair(start: datetime) -> SimpleNamespace:
    return SimpleNamespace(id=7, start_time=start, repair_type_name_ru="ПРС")


def test_current_status_takes_latest_covering_interval() -> None:
    intervals = [
        (1, datetime(2026, 9, 2), FAR, "WRK", "В работе", None),  # ещё не закрыт
        (1, datetime(2026, 10, 19, 16, 19), FAR, "DWN", "В простое", "Нет подачи"),
        (2, datetime(2026, 10, 1), datetime(2026, 10, 5), "DWN", "В простое", None),
    ]
    statuses = current_statuses(intervals, NOW)
    assert statuses[1].code == "DWN"
    assert statuses[1].reason == "Нет подачи"
    assert 2 not in statuses


def test_repair_open_while_only_idle_statuses() -> None:
    start = datetime(2026, 10, 27, 9, 0)
    intervals = [(1, start, FAR, "DWN", "В простое", "ПРС")]
    assert 1 in active_repairs({1: _repair(start)}, intervals)


def test_repair_closed_in_abai_today_ends_with_work_status() -> None:
    start = datetime(2026, 10, 27, 9, 0)
    intervals = [
        (1, start, datetime(2026, 10, 28, 15, 10), "DWN", "В простое", "ПРС"),
        (1, datetime(2026, 10, 28, 15, 10), FAR, "WRK", "В работе", None),
    ]
    assert active_repairs({1: _repair(start)}, intervals) == {}


def test_hanging_repair_ended_by_later_statuses() -> None:
    # Ремонт висит с 2024 года: после него были и работа, и консервация.
    start = datetime(2024, 1, 14, 17, 40)
    for code, name in (("WRK", "В работе"), ("CNS", "В консервации")):
        intervals = [(1, datetime(2024, 3, 1), FAR, code, name, None)]
        assert active_repairs({1: _repair(start)}, intervals) == {}


def test_outage_detection_uses_seven_day_base() -> None:
    fresh = NOW - timedelta(minutes=5)
    silent = NOW - timedelta(hours=30)  # больше суток сбоя
    assert detect_outage([silent] * 6 + [fresh] * 4, NOW) is True
    assert detect_outage([silent] * 4 + [fresh] * 6, NOW) is False
    # Станции, молчащие больше 7 суток, в базу не входят.
    assert detect_outage([NOW - timedelta(days=8)] * 9 + [fresh], NOW) is False
    assert detect_outage([], NOW) is False


def _incident(level: str, escalated: datetime | None) -> WellIncidentStatusDTO:
    item = ActiveIncidentDTO(
        id=812,
        detector_code="R2",
        detector_name_ru="Обрыв штанги",
        reason_code="moment_drop",
        level=level,
        opened_at=datetime(2026, 10, 31, 6, 0),
        detected_at=datetime(2026, 10, 31, 6, 0),
        last_seen_at=NOW,
        escalated_at=escalated,
    )
    return WellIncidentStatusDTO(
        level=level,
        title_ru="Обрыв штанги",
        since=item.opened_at,
        incidents=[item],
    )


def test_escalated_alarm_starts_at_escalation() -> None:
    escalated = datetime(2026, 10, 31, 7, 5)
    result = incident_input(_incident("alarm", escalated))
    assert result.since == escalated
    assert result.incident_id == 812
    assert incident_input(_incident("warning", None)).since == datetime(
        2026,
        10,
        31,
        6,
        0,
    )
    normal = WellIncidentStatusDTO(
        level="normal",
        title_ru="",
        since=None,
        incidents=[],
    )
    assert incident_input(normal) is None


def _row(evidence: dict | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        state="stopped",
        reason_ru="Привод остановлен (СДМО)",
        started_at=datetime(2026, 10, 19, 16, 19, 19),
        source="sdmo",
        abai_ngdu_id=12,
        evidence=evidence,
        updated_at=NOW,
    )


def _run(last_run_at: datetime, outage: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(last_run_at=last_run_at, sdmo_outage_since=outage)


def test_read_state_is_fresh() -> None:
    state = build_well_state(_row(), {12: _run(NOW - timedelta(minutes=4))}, NOW)
    assert state.code == "stopped"
    assert state.title_ru == "Остановлена"
    assert state.since == datetime(2026, 10, 19, 16, 19, 19)
    assert state.stale is False


def test_read_state_stale_when_run_is_old() -> None:
    last = NOW - timedelta(minutes=16)
    state = build_well_state(_row(), {12: _run(last)}, NOW)
    assert state.stale is True
    assert state.stale_reason == f"Состояние не обновлялось с {last:%d.%m %H:%M}"


def test_read_state_stale_on_station_ngdu_outage() -> None:
    outage = datetime(2026, 10, 27, 23, 0)
    runs = {12: _run(NOW, outage)}
    with_station = build_well_state(_row({"drive": {"abai_ngdu_id": 12}}), runs, NOW)
    assert with_station.stale_reason == "Нет данных СДМО по НГДУ с 27.10 23:00"
    assert build_well_state(_row(), runs, NOW).stale is False


class _Telemetry:
    def __init__(self, date_time: datetime | None) -> None:
        self.date_time = date_time

    async def get_last_by_well_ids(self, _ids: list[int]) -> dict:
        if self.date_time is None:
            return {}
        return {1: SimpleNamespace(qm_oil=3.3, qv_liquid=8.5, date_time=self.date_time)}


class _Regimes:
    async def get_last_by_abai_well_ids(self, _ids: list[int]) -> dict:
        return {}


def _stale(date_time: datetime | None) -> bool:
    service = WellRatesService(
        telemetry_repository=_Telemetry(date_time),
        tech_regime_repository=_Regimes(),
    )
    rates = asyncio.run(service.get_for_wells({1: 1001}, now=NOW))
    return rates[1].telemetry_stale


def test_telemetry_stale_after_seven_days_or_without_measurement() -> None:
    assert _stale(NOW - timedelta(days=6)) is False
    assert _stale(datetime(2026, 10, 16, 23, 23)) is True  # VMB_2058
    assert _stale(None) is True
