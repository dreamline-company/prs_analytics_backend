from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateWellStateDTO(RepositoryDTO):
    well_id: int
    abai_ngdu_id: int
    state: str
    source: str
    reason_code: str
    reason_ru: str
    reason_detail_ru: str | None = None
    started_at: datetime
    ended_at: datetime | None = None
    evidence: dict | None = None
    updated_at: datetime


class UpdateWellStateDTO(RepositoryDTO):
    abai_ngdu_id: int | None = None
    state: str | None = None
    source: str | None = None
    reason_code: str | None = None
    reason_ru: str | None = None
    reason_detail_ru: str | None = None
    ended_at: datetime | None = None
    evidence: dict | None = None
    updated_at: datetime | None = None


class UpsertWellDriveStateDTO(RepositoryDTO):
    station_id: int
    well_id: int | None
    running: bool | None
    since: datetime | None
    pending_running: bool | None
    pending_since: datetime | None
    reconfirming: bool
    last_sample_at: datetime | None
    last_seen_at: datetime | None
    updated_at: datetime


class UpsertWellStateRunDTO(RepositoryDTO):
    abai_ngdu_id: int
    last_run_at: datetime
    sdmo_outage_since: datetime | None
    last_duration_ms: int | None
