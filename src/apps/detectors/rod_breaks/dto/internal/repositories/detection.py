from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class CreateRodBreakRunDTO(RepositoryDTO):
    as_of_date: date
    started_at: datetime | None = None
    finished_at: datetime | None = None
    wells_scanned: int = 0
    detections_count: int = 0
    config_version: str
    status: str


class UpdateRodBreakRunDTO(RepositoryDTO):
    started_at: datetime | None = None
    finished_at: datetime | None = None
    wells_scanned: int | None = None
    detections_count: int | None = None
    status: str | None = None


class CreateRodBreakDetectionDTO(RepositoryDTO):
    run_id: int
    well_id: int
    station_sdmo_id: int | None = None
    detector_code: str = "R2"
    fired: bool
    fired_at: datetime | None = None
    failure_dt: datetime | None = None
    lead_time_hours: float | None = None
    event_class: str | None = None
    base_moment: float | None = None
    low_confidence: bool = False
    evidence: list | dict | None = None


class UpdateRodBreakDetectionDTO(RepositoryDTO):
    fired: bool | None = None
    fired_at: datetime | None = None
    failure_dt: datetime | None = None
    lead_time_hours: float | None = None
    event_class: str | None = None
    base_moment: float | None = None
    low_confidence: bool | None = None
    evidence: list | dict | None = None
