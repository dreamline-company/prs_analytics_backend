from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateDetectorDTO(RepositoryDTO):
    code: str
    name_ru: str
    source: str
    enabled: bool = True
    min_interval_sec: int = 0


class UpdateDetectorDTO(RepositoryDTO):
    name_ru: str | None = None
    source: str | None = None
    enabled: bool | None = None
    min_interval_sec: int | None = None


class OpenIncidentDTO(RepositoryDTO):
    """Аргументы upsert'а эпизода (открытие / подтверждение / эскалация)."""

    detector_code: str
    well_id: int
    entity_id: int | None = None
    reason_code: str
    level: str
    opened_at: datetime
    detected_at: datetime
    last_seen_at: datetime
    escalated_at: datetime | None = None
    config_version: str
    payload: dict | None = None


class UpdateIncidentDTO(RepositoryDTO):
    status: str | None = None
    level: str | None = None
    last_seen_at: datetime | None = None
    escalated_at: datetime | None = None
    normalized_at: datetime | None = None
    close_reason: str | None = None
    payload: dict | None = None


class CreateDetectorCursorDTO(RepositoryDTO):
    detector_code: str
    entity_id: int
    last_event_at: datetime
    last_run_at: datetime


class UpdateDetectorCursorDTO(RepositoryDTO):
    last_event_at: datetime | None = None
    last_run_at: datetime | None = None
