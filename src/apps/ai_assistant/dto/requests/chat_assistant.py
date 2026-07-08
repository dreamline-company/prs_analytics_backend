from pydantic import BaseModel


class AskChatAssistantRequestDTO(BaseModel):
    chat_id: str
    message: str


class SetRepairRequestDTO(BaseModel):
    chat_id: str
    repair_id: int
