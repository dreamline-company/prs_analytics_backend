from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class CreateCompensationDonorDTO(RepositoryDTO):
    well_id: int
    abai_ngdu_id: int
    oil_field_name: str
    gzu: str | None = None
    lift_type: str
    qn: float
    water_cut: float | None = None
    submergence_m: float | None = None
    speed: float
    step_percent: int
    gain: float
    speed_margin_checked: bool
    risk: str
    pool_date: date
    source_row: dict
    in_pool: bool = True
    has_telemetry: bool = True
    gzu_abai_id: int | None = None
    tap: int | None = None
    calculated_at: datetime | None = None


class UpdateCompensationDonorDTO(RepositoryDTO):
    risk: str | None = None


class CreateCompensationRecommendationDTO(RepositoryDTO):
    loss_well_id: int
    donor_id: int
    status: str
    loss: float
    gain: float
    speed_from: float
    speed_to: float
    distance_m: int | None = None
    opened_at: datetime


class UpdateCompensationRecommendationDTO(RepositoryDTO):
    status: str | None = None
    closed_at: datetime | None = None
    close_reason: str | None = None
    decided_at: datetime | None = None
    decided_by: str | None = None
    comment: str | None = None


class CreateCompensationLossDTO(RepositoryDTO):
    well_id: int
    abai_ngdu_id: int
    stop_kind: str
    stop_text: str | None = None
    stop_since: datetime
    loss: float | None = None
    loss_source: str | None = None
    gzu_abai_id: int | None = None
    calculated_at: datetime


class UpdateCompensationLossDTO(RepositoryDTO):
    loss: float | None = None
