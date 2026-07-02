from pydantic import BaseModel, ConfigDict, Field


class SearchNGDUByNameQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=30)
    limit: int = Field(default=20, ge=1, le=100)
