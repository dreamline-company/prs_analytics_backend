from pydantic import BaseModel, ConfigDict, Field


class ListRepairSummariesByRepairIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repair_id: int = Field(ge=1)
