from pydantic import BaseModel, ConfigDict


class OilFieldDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    prefix: str
    name: str
    ngdu_id: int
