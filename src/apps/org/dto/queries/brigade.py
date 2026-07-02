from pydantic import BaseModel, ConfigDict, Field


class ListBrigadesByNGDUIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int = Field(ge=1)
