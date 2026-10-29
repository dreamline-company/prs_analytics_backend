from typing import Literal

from pydantic import BaseModel


class WellChatAssistantResponseDTO(BaseModel):
    type: Literal["message"] = "message"
    chat_id: str
    text: str
    # Разметка ``text``: фронт рисует Markdown (без сырого HTML).
    format: Literal["markdown"] = "markdown"


class WellSetAckDTO(BaseModel):
    type: Literal["well_set"] = "well_set"
    chat_id: str
    well_id: int
