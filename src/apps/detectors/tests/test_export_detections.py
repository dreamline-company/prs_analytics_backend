"""Выгрузка детекций в CSV: улики правила разложены по колонкам."""

# ruff: noqa: DTZ001 — метки эпизодов наивные, как в БД

from datetime import date, datetime

from apps.detectors.tasks.export_detections.export_detections import (
    _finding_row,
    _incident_row,
)

INFO = {7: {"well_id": 7, "well_name": "UAZ_0032", "ngdu_name": "НГДУ-Кайнармунайгаз"}}


def test_incident_row_unpacks_payload() -> None:
    row = _incident_row(
        {
            "id": 4,
            "well_id": 7,
            "detector_code": "R9",
            "detector_name": "Перекос нагрузки",
            "detector_source": "sdmo",
            "reason_code": "load_imbalance",
            "level": "alarm",
            "status": "active",
            "opened_at": datetime(2026, 6, 7),
            "detected_at": datetime(2026, 6, 9, 12, 0),
            "last_seen_at": datetime(2026, 6, 29),
            "escalated_at": datetime(2026, 6, 8),
            "normalized_at": None,
            "close_reason": None,
            "config_version": "r9-v1",
            "payload": {"k": 0.2548, "branches": ["rel"], "baseline": {"k_p90": 0.17}},
            "st_id": 12,
            "st_sdmo_id": 300,
            "st_name": "UAZ_0032",
            "st_code": "UAZ_0032",
            "st_type": 1,
            "st_active": True,
            "st_ngdu_abai": 12,
            "st_ngdu_name": "НГДУ-Кайнармунайгаз",
        },
        INFO,
    )

    assert row["record_type"] == "incident"
    assert row["well_name"] == "UAZ_0032"
    assert row["duration_days"] == 22.0
    assert (row["r9_k"], row["r9_base_k_p90"], row["r9_branches"]) == (
        0.2548,
        0.17,
        "rel",
    )
    assert row["r2_current_ratio"] == ""
    assert row["normalized_at"] == ""


def test_finding_row_for_unknown_well_keeps_id() -> None:
    row = _finding_row(
        {
            "id": 1,
            "well_id": 99,
            "detector_code": "R10",
            "detector_name": "События ЦИТС",
            "fix_date": date(2026, 9, 30),
            "kind": "liquid_loss",
            "title": "Снижение дебита",
            "config_version": "r10-v1",
            "payload": {"event": 1, "dev": -0.6},
        },
        INFO,
    )

    assert row["record_type"] == "r10_finding"
    assert row["well_id"] == 99
    assert (row["fix_date"], row["event"], row["dev"]) == ("2026-09-30", 1, -0.6)
