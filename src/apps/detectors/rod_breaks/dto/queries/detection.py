from pydantic import BaseModel, ConfigDict, Field


class ListRodBreakDetectionsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int | None = Field(default=None, ge=1)
    only_fired: bool = False
    limit: int = Field(default=100, ge=1, le=1000)
