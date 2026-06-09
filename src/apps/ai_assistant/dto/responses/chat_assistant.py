from pydantic import BaseModel


class ChatAssistantResponseDTO(BaseModel):
    chat_id: str
    text: str
