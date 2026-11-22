from typing import Literal

from pydantic import BaseModel, Field


class HelloFrameDTO(BaseModel):
    """Первый кадр клиента: последний виденный id или null."""

    type: Literal["hello"]
    after_id: int | None = Field(default=None, ge=0)
