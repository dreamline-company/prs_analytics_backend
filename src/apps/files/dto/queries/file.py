from pydantic import BaseModel, ConfigDict, Field


class GetFileByIdQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: int = Field(ge=1)
    expires_in: int = Field(default=3600, ge=60, le=7 * 24 * 3600)
