from pydantic import BaseModel, ConfigDict


class CreateWellCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    abai_id: int
    name: str
    coords_id: int | None = None


class UpdateWellCommand(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = None
    coords_id: int | None = None
