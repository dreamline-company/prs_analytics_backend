"""Выгрузка детекций в CSV: улики правила разложены по колонкам."""

# ruff: noqa: DTZ001 — метки эпизодов наивные, как в БД

from datetime import date, datetime

from apps.detectors.tasks.export_detections.export_detections import (
    _finding_row,
    _incident_row,
)

INFO = {7: {"well_id": 7, "well_name": "UAZ_0032", "ngdu_name": "НГДУ-Кайнармунайгаз"}}

# Эпизод без проверки (left join detectors_verification ничего не нашёл).
NO_VERIFICATION = dict.fromkeys(
    (
        "v_id",
        "v_verdict",
        "v_reason",
        "v_is_final",
        "v_evidence_at",
        "v_decided_at",
        "v_final_at",
        "v_evidence",
        "v_rule_version",
    ),
)


def _r2_incident(**verification: object) -> dict:
    return {
        "id": 1170,
        "well_id": 7,
        "detector_code": "R2",
        "detector_name": "Обрыв штанги",
        "detector_source": "sdmo",
        "reason_code": "rod_break",
        "level": "alarm",
        "status": "normalized",
        "opened_at": datetime(2026, 9, 27, 15, 15),
        "detected_at": datetime(2026, 9, 27, 16, 0),
        "last_seen_at": datetime(2026, 9, 28, 2, 15),
        "escalated_at": datetime(2026, 9, 27, 16, 0),
        "normalized_at": datetime(2026, 9, 28, 2, 15),
        "close_reason": "recovered",
        "config_version": "r2-v1",
        "payload": {"base_moment": 181.0},
        "st_id": 1,
        "st_sdmo_id": 1,
        "st_name": "VMB_2791",
        "st_code": "VMB_2791",
        "st_type": 6,
        "st_active": True,
        "st_ngdu_abai": 12,
        "st_ngdu_name": "НГДУ-Кайнармунайгаз",
        **NO_VERIFICATION,
        **verification,
    }


def test_incident_row_unpacks_payload() -> None:
    row = _incident_row(
        {
            **NO_VERIFICATION,
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
        {},
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
    assert (row["verify_verdict"], row["verify_verdict_ru"]) == ("", "Не проверялась")


def test_incident_row_unpacks_verification() -> None:
    evidence = {
        "repair": [
            {
                "repair_id": 64021,
                "start": "2026-09-28T18:30:00",
                "rod_break": True,
                "work_list": "Обрыв 18-ой штанги",
            },
        ],
        "abai": [
            {
                "since": "2026-09-28T18:30:00",
                "until": None,
                "status": "DWN",
                "reason": "ПРС",
            },
        ],
    }
    history = {
        5: [
            {
                "verdict_from": None,
                "verdict_to": "failure_confirmed",
                "reason_to": "repair_rod_break",
                "changed_at": datetime(2026, 10, 7, 11, 51, 58),
            },
        ],
    }
    row = _incident_row(
        _r2_incident(
            v_id=5,
            v_verdict="failure_confirmed",
            v_reason="repair_rod_break",
            v_is_final=True,
            v_evidence_at=datetime(2026, 9, 28, 18, 30),
            v_decided_at=datetime(2026, 10, 7, 11, 51, 58),
            v_final_at=datetime(2026, 10, 7, 11, 51, 58),
            v_evidence=evidence,
            v_rule_version="r2-verify-v1",
        ),
        INFO,
        history,
    )

    assert row["verify_verdict_ru"] == "Поломка подтверждена"
    assert row["verify_reason_ru"] == "Начался ремонт, в работах «обрыв»"
    assert row["verify_hours_after_t0"] == 27.2
    assert (row["verify_repair_id"], row["verify_repair_rod_break"]) == (64021, True)
    assert row["verify_repair_start"] == "2026-09-28 18:30:00"
    assert row["verify_repair_hours_after_t0"] == 27.2
    assert (row["verify_abai_status"], row["verify_abai_reason"]) == ("DWN", "ПРС")
    assert row["verify_oil_qm_oil"] == ""
    assert row["verify_changes"] == 1
    assert row["verify_history"] == (
        "2026-10-07 11:51:58: new → failure_confirmed (repair_rod_break)"
    )


def test_incident_row_false_alarm_oil_columns() -> None:
    row = _incident_row(
        _r2_incident(
            v_id=6,
            v_verdict="false_alarm",
            v_reason="oil_ok_drive_ok",
            v_is_final=False,
            v_evidence_at=datetime(2026, 9, 28, 8, 22),
            v_decided_at=datetime(2026, 9, 28, 8, 30),
            v_final_at=None,
            v_evidence={
                "oil": {
                    "measured_at": "2026-09-27T20:22:48.833000",
                    "qm_oil": 8.0,
                    "qv_liquid": 21.0,
                    "norm": 7.0,
                    "norm_source": "median_14d",
                    "share_of_norm": 1.143,
                },
                "drive": {"work_share": 0.99, "samples": 640},
            },
            v_rule_version="r2-verify-v1",
        ),
        INFO,
        {},
    )

    assert row["verify_verdict_ru"] == "Тревога ложная"
    assert row["verify_oil_measured_at"] == "2026-09-27 20:22:48"
    assert (row["verify_oil_qm_oil"], row["verify_oil_norm"]) == (8.0, 7.0)
    assert row["verify_drive_work_share"] == 0.99
    assert (row["verify_final_at"], row["verify_repair_id"]) == ("", "")
    assert row["verify_changes"] == 0


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
