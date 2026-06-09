from dataclasses import dataclass

from shared.ai.llm.messages import HumanMessageDTO


@dataclass
class AskChatAssistantCommand:
    chat_id: str
    message: HumanMessageDTO
