"""Read-model DTO for the repair KPI endpoint (KPI ПРС card values)."""

import datetime

from pydantic import BaseModel, Field


class TechRegimeKPIDTO(BaseModel):
    """Выход на тех. режим: план, текущий замер и отклонение."""

    plan: float | None = None
    current: float | None = None
    deviation_pct: float | None = None
    unit: str | None = None


class PumpEfficiencyKPIDTO(BaseModel):
    """КПД насоса после ПРС — из AI-вердикта по динамограмме ПОСЛЕ."""

    value_pct: float | None = None
    verdict: str | None = None


class DeviationKPIDTO(BaseModel):
    """Метрика вида «процент отклонения + текстовый вердикт»."""

    deviation_pct: float | None = None
    verdict: str | None = None


class SafetyViolationsKPIDTO(BaseModel):
    """Нарушения ТБ — количество."""

    count: int = 0


class DynamogramKPIDTO(BaseModel):
    """KPI динамограмм: оценки ДО/ПОСЛЕ и направление изменения."""

    before_pct: float | None = None
    after_pct: float | None = None
    direction: str | None = None  # "better" | "worse" | "same" | None
    verdict: str | None = None


class RepairKPIMetricsDTO(BaseModel):
    """The six KPI ПРС cards."""

    tech_regime: TechRegimeKPIDTO = Field(default_factory=TechRegimeKPIDTO)
    pump_efficiency: PumpEfficiencyKPIDTO = Field(default_factory=PumpEfficiencyKPIDTO)
    por_confirmation: DeviationKPIDTO = Field(default_factory=DeviationKPIDTO)
    spo_analysis: DeviationKPIDTO = Field(default_factory=DeviationKPIDTO)
    safety_violations: SafetyViolationsKPIDTO = Field(
        default_factory=SafetyViolationsKPIDTO,
    )
    dynamogram_kpi: DynamogramKPIDTO = Field(default_factory=DynamogramKPIDTO)


class RepairKPIViewDTO(BaseModel):
    analytics_id: int
    repair_id: int
    status: str
    computed_at: datetime.datetime | None = None
    metrics: RepairKPIMetricsDTO
