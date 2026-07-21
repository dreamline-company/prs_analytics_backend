from pydantic import BaseModel, ConfigDict


class WellShortDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    abai_id: int
    name: str
