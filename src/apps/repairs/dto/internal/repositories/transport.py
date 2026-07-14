from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateRepairTransportDTO(RepositoryDTO):
    repair_id: int

    request_id: int | None = None
    operation_code: str | None = None
    operation_number: str | None = None

    status_id: int | None = None
    status_name: str | None = None
    closure_status: str | None = None

    department: str | None = None
    position: str | None = None

    operation_created_at: datetime | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    actual_date: datetime | None = None

    engine_hours: float | None = None
    mileage: float | None = None

    transport_equipment_number: int | None = None

    company: str | None = None
    division: str | None = None
    bpl: str | None = None

    well_number: str | None = None
    work_type: str | None = None

    vehicle_number: str | None = None
    vehicle_class_code: str | None = None
    vehicle_class_name: str | None = None


class UpdateRepairTransportDTO(RepositoryDTO):
    repair_id: int | None = None

    request_id: int | None = None
    operation_code: str | None = None
    operation_number: str | None = None

    status_id: int | None = None
    status_name: str | None = None
    closure_status: str | None = None

    department: str | None = None
    position: str | None = None

    operation_created_at: datetime | None = None
    planned_start_at: datetime | None = None
    planned_end_at: datetime | None = None
    actual_date: datetime | None = None

    engine_hours: float | None = None
    mileage: float | None = None

    transport_equipment_number: int | None = None

    company: str | None = None
    division: str | None = None
    bpl: str | None = None

    well_number: str | None = None
    work_type: str | None = None

    vehicle_number: str | None = None
    vehicle_class_code: str | None = None
    vehicle_class_name: str | None = None
