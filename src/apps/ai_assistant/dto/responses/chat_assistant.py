from typing import Literal

from pydantic import BaseModel


class ChatAssistantResponseDTO(BaseModel):
    type: Literal["message"] = "message"
    chat_id: str
    text: str


class RepairSetAckDTO(BaseModel):
    type: Literal["repair_set"] = "repair_set"
    chat_id: str
    repair_id: int
