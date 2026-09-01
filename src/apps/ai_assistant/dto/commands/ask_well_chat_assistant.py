from dataclasses import dataclass

from shared.ai.llm.messages import HumanMessageDTO


@dataclass
class AskWellChatAssistantCommand:
    chat_id: str
    thread_id: str
    message: HumanMessageDTO
    well_id: int | None = None
