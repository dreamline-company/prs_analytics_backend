from datetime import datetime, timedelta

from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_ACTIVE,
    DetectorIncident,
)
from apps.detectors.services.well_incident_status import (
    NORMAL_TITLE_RU,
    UNKNOWN_TITLE_RU,
    WELL_STATE_NORMAL,
    build_well_incident_status,
)

NAMES = {"R2": "Обрыв штанги", "R9": "Перекос нагрузки на ходе (ШГН)"}
DAY = datetime(2026, 8, 20)  # noqa: DTZ001


def _incident(
    *,
    incident_id: int = 1,
    detector_code: str = "R9",
    level: str = INCIDENT_LEVEL_WARNING,
    opened_at: datetime = DAY,
) -> DetectorIncident:
    return DetectorIncident(
        id=incident_id,
        detector_code=detector_code,
        well_id=1,
        entity_id=100,
        reason_code="load_imbalance",
        level=level,
        status=INCIDENT_STATUS_ACTIVE,
        opened_at=opened_at,
        detected_at=opened_at + timedelta(hours=4),
        last_seen_at=opened_at + timedelta(days=1),
        escalated_at=None,
        config_version="r9-v1",
    )


def test_no_incidents_is_normal() -> None:
    status = build_well_incident_status([], NAMES)

    assert status.level == WELL_STATE_NORMAL
    assert status.title_ru == NORMAL_TITLE_RU
    assert status.since is None
    assert status.incidents == []


def test_single_incident_drives_title_and_since() -> None:
    status = build_well_incident_status([_incident(detector_code="R2")], NAMES)

    assert status.level == INCIDENT_LEVEL_WARNING
    assert status.title_ru == "Обрыв штанги"
    assert status.since == DAY
    assert len(status.incidents) == 1
    assert status.incidents[0].detector_name_ru == "Обрыв штанги"


def test_alarm_wins_over_earlier_warning() -> None:
    """Шапку определяет худший уровень, а не тот, что начался раньше."""
    warning = _incident(
        incident_id=1,
        detector_code="R2",
        level=INCIDENT_LEVEL_WARNING,
        opened_at=DAY - timedelta(days=5),
    )
    alarm = _incident(
        incident_id=2,
        detector_code="R9",
        level=INCIDENT_LEVEL_ALARM,
        opened_at=DAY,
    )

    status = build_well_incident_status([warning, alarm], NAMES)

    assert status.level == INCIDENT_LEVEL_ALARM
    assert status.title_ru == NAMES["R9"]
    assert status.since == DAY
    # Список отдаётся целиком и отсортирован — худший первым.
    assert [item.id for item in status.incidents] == [2, 1]


def test_same_level_takes_the_earliest() -> None:
    later = _incident(incident_id=1, detector_code="R2", opened_at=DAY)
    earlier = _incident(
        incident_id=2,
        detector_code="R9",
        opened_at=DAY - timedelta(days=3),
    )

    status = build_well_incident_status([later, earlier], NAMES)

    assert status.since == DAY - timedelta(days=3)
    assert status.title_ru == NAMES["R9"]


def test_unknown_level_is_not_treated_as_normal() -> None:
    """Правило новее этого кода не должно проваливаться в «штатный режим»."""
    unknown = _incident(incident_id=1, level="critical")
    alarm = _incident(incident_id=2, level=INCIDENT_LEVEL_ALARM)

    status = build_well_incident_status([unknown, alarm], NAMES)

    assert status.level == "critical"


def test_detector_missing_from_registry_falls_back_to_generic_title() -> None:
    status = build_well_incident_status([_incident(detector_code="R42")], NAMES)

    assert status.title_ru == UNKNOWN_TITLE_RU
    assert status.incidents[0].detector_name_ru is None
