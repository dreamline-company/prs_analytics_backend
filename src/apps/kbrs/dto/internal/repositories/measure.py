from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateKbrsMeasureDTO(RepositoryDTO):
    measure_id: int
    owner_id: int
    device_id: int
    device_type: int = 0
    raw_size: int = 0
    well_number: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    row_count: int = 0
    status: str
    raw_file_id: int | None = None
    chart_json_file_id: int | None = None
    notes_file_id: int | None = None
    passport_file_id: int | None = None
    fetched_at: datetime


class UpdateKbrsMeasureDTO(RepositoryDTO):
    raw_size: int | None = None
    well_number: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    row_count: int | None = None
    status: str | None = None
    raw_file_id: int | None = None
    chart_json_file_id: int | None = None
    notes_file_id: int | None = None
    passport_file_id: int | None = None
    fetched_at: datetime | None = None
