from pydantic import BaseModel


class MainKPIStatusDTO(BaseModel):
    stops: float
    disconnecting: float
    accident_risk: float
    oil_production_loss: float
    losses_compensation: float


class MainKPIIsNewDTO(BaseModel):
    data: MainKPIStatusDTO
    is_new: bool
