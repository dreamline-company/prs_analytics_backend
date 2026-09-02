from pydantic import BaseModel


class AskWellChatAssistantRequestDTO(BaseModel):
    chat_id: str
    message: str


class SetWellRequestDTO(BaseModel):
    chat_id: str
    well_id: int
