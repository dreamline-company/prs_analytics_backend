from pydantic import BaseModel, ConfigDict, Field


class SearchWellsByNameQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=15)
    limit: int = Field(default=20, ge=1, le=100)


class GetWellsMatrixQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int = Field(ge=1)


class GetWellCardQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    well_id: int = Field(ge=1)


class GetWellMatrixIncidentsQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ngdu_id: int = Field(ge=1)
