from typing import Literal

from pydantic import BaseModel


class WellTwinStatus(BaseModel):
    status_name: str


class WellProduction(BaseModel):
    production_number: float
    production_type: Literal["increasing", "decreasing"]


class WellTelemetryDTO(BaseModel):
    liquid_flow_rate: float | None = None
    oil_flow_rate: float | None = None
    water_cut: float | None = None
    dynamic_pressure: float | None = None
    bottomhole_pressure: float | None = None
    temperature: float | None = None
    epn_frequency: float | None = None
    pump_load: float | None = None


class WellTwinDTO(BaseModel):
    well_name: str
    status: WellTwinStatus
    work_regime: str
    production_number: float
    equipment: list[str]
    telemetry: WellTelemetryDTO
