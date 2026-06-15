from pydantic import BaseModel, ConfigDict


class RepositoryDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")
