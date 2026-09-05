from pydantic import BaseModel, ConfigDict, Field


class ListOilFieldsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int | None = Field(default=None, ge=1)
