from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateSPODTO(RepositoryDTO):
    file_id: int
    chart_file_id: int | None = None
    chart_json_file_id: int | None = None
    notes_file_id: int | None = None
    passport_file_id: int | None = None
    snapshot_time: datetime
    well_id: int


class UpdateSPODTO(RepositoryDTO):
    file_id: int | None = None
    chart_file_id: int | None = None
    chart_json_file_id: int | None = None
    notes_file_id: int | None = None
    passport_file_id: int | None = None
    snapshot_time: datetime | None = None
    well_id: int | None = None
