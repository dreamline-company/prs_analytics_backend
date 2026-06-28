from pydantic import BaseModel, ConfigDict, Field


class ListRepairsByWellIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)
