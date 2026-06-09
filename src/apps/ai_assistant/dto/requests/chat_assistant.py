from pydantic import BaseModel


class AskChatAssistantRequestDTO(BaseModel):
    chat_id: str
    message: str
