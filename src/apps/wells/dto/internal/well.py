from pydantic import BaseModel, ConfigDict


class WellShortDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    name: str


class WellMatrixItemDTO(BaseModel):
    id: int
    name: str
    is_on_repair: bool
    repair_id: int | None = None
