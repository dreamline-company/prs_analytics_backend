import json
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Path
from pydantic import ValidationError
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.ai_assistant.ai.chat_assistant.agent import (
    chat_assistant_agent,
    get_agent_config,
)
from apps.ai_assistant.dto.commands.ask_chat_assistant import (
    AskChatAssistantCommand,
)
from apps.ai_assistant.dto.requests.chat_assistant import (
    AskChatAssistantRequestDTO,
    SetRepairRequestDTO,
)
from apps.ai_assistant.dto.responses.chat_assistant import (
    ChatAssistantResponseDTO,
    RepairSetAckDTO,
)
from apps.ai_assistant.use_cases.ask_chat_assistant import AskChatAssistantUseCase
from core import get_logger
from shared.ai.llm.messages import HumanMessageDTO
from shared.errors import WSError

logger = get_logger(__name__)
ws_router = APIRouter(prefix="/prs")


class BadRequestDataError(WSError):
    message = "Bad Request Data."
    close_code = status.WS_1007_INVALID_FRAME_PAYLOAD_DATA


def _new_thread_id(chat_id: str) -> str:
    return f"{chat_id}:{uuid4().hex}"


@dataclass
class ChatSession:
    chat_id: str
    repair_id: int | None = None
    thread_id: str | None = None

    def bind_repair(self, repair_id: int) -> None:
        self.repair_id = repair_id
        self.thread_id = _new_thread_id(self.chat_id)

    def ensure_thread(self) -> str:
        if self.thread_id is None:
            self.thread_id = _new_thread_id(self.chat_id)
        return self.thread_id


def _parse_payload(data: object) -> SetRepairRequestDTO | AskChatAssistantRequestDTO:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise BadRequestDataError(details={"json": str(e)}) from e
    if not isinstance(data, dict):
        raise BadRequestDataError(details={"payload": "expected JSON object"})
    try:
        if "repair_id" in data:
            return SetRepairRequestDTO(**data)
        return AskChatAssistantRequestDTO(**data)
    except ValidationError as ve:
        raise BadRequestDataError(details={"validation": str(ve)}) from ve


async def _handle_message(
    ws: WebSocket,
    session: ChatSession,
    request: AskChatAssistantRequestDTO,
    use_case: AskChatAssistantUseCase,
) -> None:
    thread_id = session.ensure_thread()
    result = await use_case.execute(
        command=AskChatAssistantCommand(
            chat_id=session.chat_id,
            thread_id=thread_id,
            repair_id=session.repair_id,
            message=HumanMessageDTO(content=request.message),
        ),
    )
    await ws.send_json(
        ChatAssistantResponseDTO(
            chat_id=session.chat_id,
            text=result.content,
        ).model_dump(mode="json"),
    )


async def _handle_set_repair(
    ws: WebSocket,
    session: ChatSession,
    request: SetRepairRequestDTO,
) -> None:
    session.bind_repair(request.repair_id)
    await ws.send_json(
        RepairSetAckDTO(
            chat_id=session.chat_id,
            repair_id=request.repair_id,
        ).model_dump(mode="json"),
    )


@ws_router.websocket("/chat/{chat_id}")
async def assistant_chat_websocket(
    ws: WebSocket,
    chat_id: Annotated[str, Path(description="Chat UUID")],
) -> None:
    await ws.accept()
    use_case = AskChatAssistantUseCase(
        agent=chat_assistant_agent,
        config_factory=get_agent_config,
    )
    session = ChatSession(chat_id=chat_id)

    try:
        while True:
            data = await ws.receive_json()
            request = _parse_payload(data)
            if isinstance(request, SetRepairRequestDTO):
                await _handle_set_repair(ws, session, request)
            else:
                await _handle_message(ws, session, request, use_case)

    except BadRequestDataError as e:
        logger.debug("Bad Request Data: %s", e.to_jsons())
        await ws.close(code=e.close_code, reason=e.to_jsons()[:100])

    except WebSocketDisconnect:
        ...
