from typing import Literal

from pydantic import BaseModel, Field, StrictInt

from apps.notifications.constants import MAX_ID


class HelloFrameDTO(BaseModel):
    """Первый кадр клиента: последний виденный id или null."""

    type: Literal["hello"]
    # Строго целое: true/"5" — кривой hello (1007), а не id 1.
    after_id: StrictInt | None = Field(default=None, ge=0, le=MAX_ID)
