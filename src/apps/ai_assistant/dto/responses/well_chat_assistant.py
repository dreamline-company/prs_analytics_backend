from typing import Literal

from pydantic import BaseModel


class WellChatAssistantResponseDTO(BaseModel):
    type: Literal["message"] = "message"
    chat_id: str
    text: str


class WellSetAckDTO(BaseModel):
    type: Literal["well_set"] = "well_set"
    chat_id: str
    well_id: int
