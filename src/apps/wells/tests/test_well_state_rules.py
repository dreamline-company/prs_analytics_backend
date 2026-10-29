"""Правила итогового состояния скважины: приоритеты и начало по данным."""

# Времена в тестах — местные наивные, как в СДМО и ABAI.
# ruff: noqa: DTZ001

from datetime import datetime, timedelta

from apps.wells.models.well_state import (
    SOURCE_ABAI,
    SOURCE_DETECTOR,
    SOURCE_REPAIR,
    SOURCE_SDMO,
    STATE_ALARM,
    STATE_INACTIVE,
    STATE_NO_LINK,
    STATE_REPAIR,
    STATE_STOPPED,
    STATE_WARNING,
    STATE_WORKING,
)
from apps.wells.services.well_state.rules import (
    AbaiStatusInput,
    DriveInput,
    IncidentInput,
    RepairInput,
    SdmoInput,
    WellInputs,
    decide,
)

NOW = datetime(2026, 10, 28, 10, 40)
KMG = 12


def _wrk(since: datetime = datetime(2026, 10, 15, 12, 37)) -> AbaiStatusInput:
    return AbaiStatusInput("WRK", "В работе", None, since)


def _dwn(reason: str, since: datetime) -> AbaiStatusInput:
    return AbaiStatusInput("DWN", "В простое", reason, since)


def _sdmo(
    *,
    running: bool | None,
    since: datetime,
    known: bool = True,
    last_seen: datetime = NOW,
    outage_since: datetime | None = None,
) -> SdmoInput:
    drive = DriveInput(
        station_id=1284,
        abai_ngdu_id=KMG,
        running=running,
        since=since,
        known=known,
        last_sample_at=last_seen,
    )
    return SdmoInput(
        has_active_station=True,
        drive=drive,
        last_seen_at=last_seen,
        outage_since=outage_since,
    )


def _silent(last_seen: datetime) -> SdmoInput:
    return _sdmo(running=True, since=NOW - timedelta(days=30), known=False,
                 last_seen=last_seen)  # fmt: skip


def test_vmb_2058_stopped_by_sdmo_with_abai_reason() -> None:
    stop = datetime(2026, 10, 19, 16, 19, 19)
    decision = decide(
        WellInputs(
            abai=_dwn("Нет подачи", datetime(2026, 10, 19, 16, 19)),
            sdmo=_sdmo(running=False, since=stop),
        ),
        NOW,
    )
    assert decision.state == STATE_STOPPED
    assert decision.source == SOURCE_SDMO
    assert decision.started_at == stop
    # Оператору — без источников, источники — в подробностях.
    assert decision.reason_ru == "Остановлена: Нет подачи"
    assert decision.reason_detail_ru == "Привод остановлен (СДМО), в ABAI «Нет подачи»"


def test_vmb_2775_abai_repair_status_but_drive_running_is_warning() -> None:
    # Ремонт закрыт по телеметрии (end_time есть) — сборщик его не отдаёт;
    # в ABAI открыт простой «ПРС», привод работает с 26.10.
    decision = decide(
        WellInputs(
            abai=_dwn("ПРС", datetime(2026, 10, 20, 10, 40)),
            sdmo=_sdmo(running=True, since=datetime(2026, 10, 26, 9, 0)),
        ),
        NOW,
    )
    assert decision.state == STATE_WARNING
    assert decision.reason_code == "abai_conflict"
    assert decision.reason_ru == "Привод работает, хотя числится в простое: ПРС"
    assert decision.reason_detail_ru == "В ABAI «ПРС», привод работает"
    assert decision.started_at == datetime(2026, 10, 26, 9, 0)


def test_repair_with_stopped_drive_is_repair() -> None:
    start = datetime(2026, 10, 27, 9, 0)
    decision = decide(
        WellInputs(
            abai=_dwn("ПРС", start),
            repair=RepairInput(7401, "Ликвидация обрыва", start),
            sdmo=_sdmo(running=False, since=start),
        ),
        NOW,
    )
    assert decision.state == STATE_REPAIR
    assert decision.source == SOURCE_REPAIR
    assert decision.reason_ru == "Ремонт: Ликвидация обрыва"
    assert decision.started_at == start


def test_repair_with_running_drive_is_warning() -> None:
    start = datetime(2026, 10, 27, 9, 0)
    decision = decide(
        WellInputs(
            abai=_dwn("ПРС", start),
            repair=RepairInput(7401, "Смена насоса", start),
            sdmo=_sdmo(running=True, since=datetime(2026, 10, 28, 8, 0)),
        ),
        NOW,
    )
    assert decision.state == STATE_WARNING
    assert decision.source == SOURCE_REPAIR
    assert decision.reason_ru == (
        "Привод работает, хотя числится в ремонте: Смена насоса"
    )
    assert decision.reason_detail_ru == "В ABAI ремонт «Смена насоса», привод работает"


def test_repair_without_sdmo_is_repair() -> None:
    start = datetime(2026, 10, 27, 9, 0)
    decision = decide(
        WellInputs(abai=_dwn("ПРС", start), repair=RepairInput(1, None, start)),
        NOW,
    )
    assert decision.state == STATE_REPAIR
    assert decision.reason_ru == "Ремонт: вид работ не указан"
    assert decision.reason_detail_ru == decision.reason_ru


def test_conservation_with_stopped_drive_is_inactive() -> None:
    since = datetime(2024, 3, 12)
    decision = decide(
        WellInputs(
            abai=AbaiStatusInput("CNS", "В консервации", None, since),
            sdmo=_sdmo(running=False, since=datetime(2026, 9, 28)),
        ),
        NOW,
    )
    assert decision.state == STATE_INACTIVE
    assert decision.reason_ru == "В консервации"
    assert decision.reason_detail_ru == "В консервации (ABAI)"
    assert decision.started_at == since


def test_conservation_with_running_drive_is_warning() -> None:
    decision = decide(
        WellInputs(
            abai=AbaiStatusInput("CNS", "В консервации", None, datetime(2024, 3, 12)),
            sdmo=_sdmo(running=True, since=datetime(2026, 10, 28, 9, 0)),
        ),
        NOW,
    )
    assert decision.state == STATE_WARNING
    assert decision.reason_ru == "Привод работает, хотя числится: В консервации"
    assert decision.reason_detail_ru == "В ABAI «В консервации», привод работает"


def test_unknown_and_empty_status_codes_are_inactive() -> None:
    for code in ("XYZ", None):
        decision = decide(
            WellInputs(abai=AbaiStatusInput(code, "Что-то новое", None, NOW)),
            NOW,
        )
        assert decision.state == STATE_INACTIVE


def test_periodic_with_stopped_drive_is_working() -> None:
    decision = decide(
        WellInputs(
            abai=AbaiStatusInput("PEXP", "Периодическая эксплуатация", None, NOW),
            sdmo=_sdmo(running=False, since=NOW - timedelta(hours=2)),
        ),
        NOW,
    )
    assert decision.state == STATE_WORKING
    assert decision.reason_code == "abai_periodic"


def test_dmg_idle_without_sdmo_is_stopped_by_abai() -> None:
    since = datetime(2026, 10, 26, 11, 0)
    decision = decide(WellInputs(abai=_dwn("Ожидание ГИС", since)), NOW)
    assert decision.state == STATE_STOPPED
    assert decision.source == SOURCE_ABAI
    assert decision.reason_ru == "Простой: Ожидание ГИС"
    assert decision.reason_detail_ru == "Простой: Ожидание ГИС (ABAI)"
    assert decision.started_at == since


def test_work_without_sdmo_is_working_by_abai() -> None:
    decision = decide(WellInputs(abai=_wrk()), NOW)
    assert decision.state == STATE_WORKING
    assert decision.source == SOURCE_ABAI
    assert decision.reason_ru == "Работает в штатном режиме"
    assert decision.reason_detail_ru == "В работе (ABAI)"


def test_station_silent_two_hours_is_no_link() -> None:
    last = NOW - timedelta(hours=2)
    decision = decide(WellInputs(abai=_wrk(), sdmo=_silent(last)), NOW)
    assert decision.state == STATE_NO_LINK
    assert decision.started_at == last
    assert decision.reason_ru == f"Нет связи со станцией с {last:%d.%m %H:%M}"
    assert decision.reason_detail_ru == f"Нет данных СДМО с {last:%d.%m %H:%M}"


def test_station_silent_eight_days_falls_back_to_abai() -> None:
    last = NOW - timedelta(days=8)
    decision = decide(WellInputs(abai=_wrk(), sdmo=_silent(last)), NOW)
    assert decision.state == STATE_WORKING
    assert decision.source == SOURCE_ABAI
    assert decision.reason_ru == (
        f"Работает, нет связи со станцией с {last:%d.%m %H:%M}"
    )
    assert decision.reason_detail_ru.endswith(f"СДМО молчит с {last:%d.%m %H:%M}")


def test_silent_station_with_abai_idle_is_stopped_not_no_link() -> None:
    decision = decide(
        WellInputs(
            abai=_dwn("Нет подачи", NOW - timedelta(days=3)),
            sdmo=_silent(NOW - timedelta(hours=2)),
        ),
        NOW,
    )
    assert decision.state == STATE_STOPPED
    assert decision.source == SOURCE_ABAI


def test_outage_freezes_drive_state() -> None:
    since = NOW - timedelta(days=5)
    decision = decide(
        WellInputs(
            abai=_wrk(),
            sdmo=_sdmo(
                running=True,
                since=since,
                known=True,
                last_seen=NOW - timedelta(hours=3),
                outage_since=NOW - timedelta(hours=3),
            ),
        ),
        NOW,
    )
    assert decision.state == STATE_WORKING
    assert decision.source == SOURCE_SDMO


def test_outage_does_not_make_silenced_station_no_link() -> None:
    # Станция замолчала вместе со всем НГДУ, привод неизвестен — по ABAI.
    outage = NOW - timedelta(hours=3)
    decision = decide(
        WellInputs(
            abai=_wrk(),
            sdmo=_sdmo(
                running=None,
                since=NOW,
                known=False,
                last_seen=outage,
                outage_since=outage,
            ),
        ),
        NOW,
    )
    assert decision.state == STATE_WORKING
    assert decision.source == SOURCE_ABAI


def test_station_silent_before_outage_stays_no_link() -> None:
    last = NOW - timedelta(days=2)
    decision = decide(
        WellInputs(
            abai=_wrk(),
            sdmo=_sdmo(
                running=True,
                since=NOW - timedelta(days=9),
                known=False,
                last_seen=last,
                outage_since=NOW - timedelta(hours=3),
            ),
        ),
        NOW,
    )
    assert decision.state == STATE_NO_LINK
    assert decision.started_at == last


def test_no_status_and_no_sdmo_is_no_link() -> None:
    decision = decide(WellInputs(), NOW)
    assert decision.state == STATE_NO_LINK
    assert decision.reason_code == "no_data"
    assert decision.reason_ru == "Нет данных о работе скважины"
    assert decision.reason_detail_ru == "Нет ни статуса ABAI, ни данных СДМО"
    assert decision.started_at is None


def test_no_status_uses_sdmo() -> None:
    decision = decide(
        WellInputs(sdmo=_sdmo(running=True, since=NOW - timedelta(hours=1))),
        NOW,
    )
    assert decision.state == STATE_WORKING
    assert decision.source == SOURCE_SDMO
    assert decision.reason_ru == "Работает в штатном режиме"
    assert decision.reason_detail_ru == "Привод работает"


def test_alarm_beats_no_link_and_mentions_silence() -> None:
    last = NOW - timedelta(hours=2)
    decision = decide(
        WellInputs(
            abai=_wrk(),
            incident=IncidentInput("alarm", "Обрыв штанги", NOW - timedelta(hours=5)),
            sdmo=_silent(last),
        ),
        NOW,
    )
    assert decision.state == STATE_ALARM
    assert decision.source == SOURCE_DETECTOR
    assert decision.reason_ru == (
        f"Обрыв штанги; нет связи со станцией с {last:%d.%m %H:%M}"
    )
    assert decision.reason_detail_ru == (
        f"Обрыв штанги; нет данных СДМО с {last:%d.%m %H:%M}"
    )


def test_unknown_incident_level_is_alarm() -> None:
    decision = decide(
        WellInputs(abai=_wrk(), incident=IncidentInput("critical", "Новое", NOW)),
        NOW,
    )
    assert decision.state == STATE_ALARM


def test_stopped_beats_alarm() -> None:
    decision = decide(
        WellInputs(
            abai=_wrk(),
            incident=IncidentInput("alarm", "Обрыв штанги", NOW - timedelta(hours=5)),
            sdmo=_sdmo(running=False, since=NOW - timedelta(hours=1)),
        ),
        NOW,
    )
    assert decision.state == STATE_STOPPED


def test_detector_warning_on_working_well() -> None:
    decision = decide(
        WellInputs(
            abai=_wrk(),
            incident=IncidentInput("warning", "Перекос нагрузки", NOW),
            sdmo=_sdmo(running=True, since=NOW - timedelta(days=2)),
        ),
        NOW,
    )
    assert decision.state == STATE_WARNING
    assert decision.reason_code == "detector_warning"


def test_wrk_after_conservation_stopped_since_status_change() -> None:
    wrk_since = datetime(2026, 10, 28, 8, 0)
    decision = decide(
        WellInputs(
            abai=_wrk(wrk_since),
            sdmo=_sdmo(running=False, since=datetime(2026, 9, 28)),
        ),
        NOW,
    )
    assert decision.state == STATE_STOPPED
    assert decision.started_at == wrk_since


def test_drive_stopped_without_abai_idle_has_no_abai_reason() -> None:
    decision = decide(
        WellInputs(
            abai=_wrk(),
            sdmo=_sdmo(running=False, since=NOW - timedelta(hours=3)),
        ),
        NOW,
    )
    assert decision.state == STATE_STOPPED
    assert decision.reason_ru == "Привод остановлен"
    assert decision.reason_detail_ru == "Привод остановлен (СДМО)"
