from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateRepairTypeDTO(RepositoryDTO):
    abai_id: int
    name_ru: str
    name_ru_short: str | None


class UpdateRepairTypeDTO(RepositoryDTO):
    name_ru: str | None = None
    name_ru_short: str | None = None


class CreateRepairDTO(RepositoryDTO):
    abai_id: int
    abai_well_id: int
    work_list: str | None = None
    work_plan: str | None = None
    repair_type_id: int
    start_time: datetime
    end_time: datetime | None = None


class UpdateRepairDTO(RepositoryDTO):
    work_list: str | None = None
    work_plan: str | None = None
    repair_type_id: int | None = None
    start_time: datetime | None = None
    end_time: datetime | None = None
    abai_deleted_at: datetime | None = None
    closed_by_telemetry_at: datetime | None = None
