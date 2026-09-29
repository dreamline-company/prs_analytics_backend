"""Ответы контура компенсации. Дебиты и приросты — т/сут."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field


class CompensationWellDTO(BaseModel):
    id: int
    well_name: str


class CompensationOilFieldDTO(BaseModel):
    prefix: str
    name: str


class CompensationNgduDTO(BaseModel):
    id: int
    name: str


class CompensationSpeedDTO(BaseModel):
    """Разгон донора: было -> станет, в единицах привода."""

    model_config = ConfigDict(populate_by_name=True)

    from_: float = Field(alias="from")
    to: float
    delta: float
    step_percent: int
    units: str | None


class CompensationStopReasonDTO(BaseModel):
    kind: str  # idle — простой в ABAI, repair — незавершённый ремонт
    text: str | None  # причина простоя или вид ремонта
    since: datetime  # местное время


class CompensationPairDonorDTO(BaseModel):
    """Донор, закреплённый за потерей."""

    recommendation_id: int
    well: CompensationWellDTO
    gain: float
    risk: str
    distance_m: int | None
    status: str


class CompensationContourLossDTO(BaseModel):
    value: float
    plan_date: date


class CompensationContourCompensatedDTO(BaseModel):
    value: float
    donors: int


class CompensationContourDTO(BaseModel):
    as_of: datetime
    well: CompensationWellDTO
    state: str  # stopped / no_plan / working
    reason: CompensationStopReasonDTO | None
    losses: CompensationContourLossDTO | None
    compensated: CompensationContourCompensatedDTO
    efficiency_percent: float | None
    potential: float | None  # непокрытая часть потери
    main_donors: list[CompensationPairDonorDTO]


class CompensationRecommendationDTO(BaseModel):
    recommendation_id: int
    status: str
    donor: CompensationWellDTO
    action: str = "increase_speed"
    speed: CompensationSpeedDTO
    gain: float
    risk: str
    loss_well: CompensationWellDTO
    distance_m: int | None


class CompensationRecommendationsDTO(BaseModel):
    available_potential: float  # прирост всех работающих доноров
    used: float  # применённые пары
    total: int  # открытых пар всего
    items: list[CompensationRecommendationDTO]


class CompensationDonorConstraintsDTO(BaseModel):
    speed_margin_checked: bool
    submergence_m: float | None
    water_cut_percent: float | None


class CompensationDonorDTO(BaseModel):
    donor_id: int
    well: CompensationWellDTO
    oil_field: CompensationOilFieldDTO
    ngdu: CompensationNgduDTO
    gzu: str | None
    lift_type: str
    status: str
    risk: str
    speed: CompensationSpeedDTO
    qn: float  # Qн, от которого посчитан прирост
    gain: float
    gain_kind: str = "scenario"
    constraints: CompensationDonorConstraintsDTO
    loss_well: CompensationWellDTO | None


class CompensationDonorsSummaryDTO(BaseModel):
    candidates: int
    available_potential: float
    used: float
    awaiting_approval: float
    realized_percent: float
    pool_calculated_at: date | None


class CompensationDonorsCountsDTO(BaseModel):
    all: int
    pending: int = 0
    accepted: int = 0
    applied: int = 0
    rejected: int = 0
    reserve: int = 0


class CompensationDonorsDTO(BaseModel):
    summary: CompensationDonorsSummaryDTO
    counts: CompensationDonorsCountsDTO
    items: list[CompensationDonorDTO]


class CompensationLossDTO(BaseModel):
    well: CompensationWellDTO
    oil_field: CompensationOilFieldDTO
    reason: CompensationStopReasonDTO
    loss: float | None  # план Qн; None — плана нет
    plan_date: date | None
    covered: float
    uncovered: float | None
    donors: list[CompensationPairDonorDTO]


class CompensationLossesSummaryDTO(BaseModel):
    wells: int
    wells_without_plan: int
    loss: float
    covered: float
    uncovered: float


class CompensationLossesDTO(BaseModel):
    summary: CompensationLossesSummaryDTO
    items: list[CompensationLossDTO]
