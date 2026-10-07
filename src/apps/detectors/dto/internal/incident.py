from datetime import datetime

from pydantic import BaseModel, ConfigDict


class IncidentVerificationDTO(BaseModel):
    """Отметка проверки эпизода независимыми данными (``detectors_verification``).

    verdict: pending | false_alarm | failure_likely | failure_confirmed |
    undetermined; reason — почему (oil_ok_drive_ok, drive_stopped, repair, …);
    evidence — найденные доказательства (oil / drive / abai / repair).
    """

    model_config = ConfigDict(from_attributes=True)

    verdict: str
    reason: str | None
    is_final: bool
    evidence_at: datetime | None
    decided_at: datetime
    final_at: datetime | None
    evidence: dict | None
    rule_version: str


class DetectorIncidentDTO(BaseModel):
    """Эпизод детекции для чтения наружу — строка ``detectors_incident``.

    ``detector_name_ru`` подставляется на чтении из реестра правил: в таблице
    инцидентов лежит только код. ``verification`` — отметка проверки, если
    эпизод уже проверялся (пока только R2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    detector_code: str
    detector_name_ru: str | None = None
    well_id: int
    entity_id: int | None
    reason_code: str

    level: str
    status: str

    opened_at: datetime
    detected_at: datetime
    last_seen_at: datetime
    escalated_at: datetime | None
    normalized_at: datetime | None
    close_reason: str | None

    config_version: str
    payload: dict | None

    verification: IncidentVerificationDTO | None = None
