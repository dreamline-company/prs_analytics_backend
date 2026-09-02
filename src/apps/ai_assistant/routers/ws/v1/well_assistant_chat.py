"""WS-чат скважинного ассистента — калька с ПРС-чата.

Протокол: клиент шлёт JSON-объекты; ``{"chat_id", "well_id"}`` привязывает
скважину (новый thread у агента), ``{"chat_id", "message"}`` — вопрос.
"""

import json
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from fastapi import APIRouter, Path
from pydantic import ValidationError
from starlette import status
from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.ai_assistant.ai.well_chat_assistant.agent import (
    get_well_agent_config,
    well_chat_assistant_agent,
)
from apps.ai_assistant.dto.commands.ask_well_chat_assistant import (
    AskWellChatAssistantCommand,
)
from apps.ai_assistant.dto.requests.well_chat_assistant import (
    AskWellChatAssistantRequestDTO,
    SetWellRequestDTO,
)
from apps.ai_assistant.dto.responses.well_chat_assistant import (
    WellChatAssistantResponseDTO,
    WellSetAckDTO,
)
from apps.ai_assistant.use_cases.ask_well_chat_assistant import (
    AskWellChatAssistantUseCase,
)
from core import get_logger
from shared.ai.llm.messages import HumanMessageDTO
from shared.errors import WSError

logger = get_logger(__name__)
ws_router = APIRouter(prefix="/wells")


class BadRequestDataError(WSError):
    message = "Bad Request Data."
    close_code = status.WS_1007_INVALID_FRAME_PAYLOAD_DATA


def _new_thread_id(chat_id: str) -> str:
    return f"{chat_id}:{uuid4().hex}"


@dataclass
class WellChatSession:
    chat_id: str
    well_id: int | None = None
    thread_id: str | None = None

    def bind_well(self, well_id: int) -> None:
        self.well_id = well_id
        self.thread_id = _new_thread_id(self.chat_id)

    def ensure_thread(self) -> str:
        if self.thread_id is None:
            self.thread_id = _new_thread_id(self.chat_id)
        return self.thread_id


def _parse_payload(
    data: object,
) -> SetWellRequestDTO | AskWellChatAssistantRequestDTO:
    if isinstance(data, str):
        try:
            data = json.loads(data)
        except json.JSONDecodeError as e:
            raise BadRequestDataError(details={"json": str(e)}) from e
    if not isinstance(data, dict):
        raise BadRequestDataError(details={"payload": "expected JSON object"})
    try:
        if "well_id" in data:
            return SetWellRequestDTO(**data)
        return AskWellChatAssistantRequestDTO(**data)
    except ValidationError as ve:
        raise BadRequestDataError(details={"validation": str(ve)}) from ve


async def _handle_message(
    ws: WebSocket,
    session: WellChatSession,
    request: AskWellChatAssistantRequestDTO,
    use_case: AskWellChatAssistantUseCase,
) -> None:
    thread_id = session.ensure_thread()
    result = await use_case.execute(
        command=AskWellChatAssistantCommand(
            chat_id=session.chat_id,
            thread_id=thread_id,
            well_id=session.well_id,
            message=HumanMessageDTO(content=request.message),
        ),
    )
    await ws.send_json(
        WellChatAssistantResponseDTO(
            chat_id=session.chat_id,
            text=result.content,
        ).model_dump(mode="json"),
    )


async def _handle_set_well(
    ws: WebSocket,
    session: WellChatSession,
    request: SetWellRequestDTO,
) -> None:
    session.bind_well(request.well_id)
    await ws.send_json(
        WellSetAckDTO(
            chat_id=session.chat_id,
            well_id=request.well_id,
        ).model_dump(mode="json"),
    )


@ws_router.websocket("/chat/{chat_id}")
async def well_assistant_chat_websocket(
    ws: WebSocket,
    chat_id: Annotated[str, Path(description="Chat UUID")],
) -> None:
    await ws.accept()
    use_case = AskWellChatAssistantUseCase(
        agent=well_chat_assistant_agent,
        config_factory=get_well_agent_config,
    )
    session = WellChatSession(chat_id=chat_id)

    try:
        while True:
            data = await ws.receive_json()
            request = _parse_payload(data)
            if isinstance(request, SetWellRequestDTO):
                await _handle_set_well(ws, session, request)
            else:
                await _handle_message(ws, session, request, use_case)

    except BadRequestDataError as e:
        logger.debug("Bad Request Data: %s", e.to_jsons())
        await ws.close(code=e.close_code, reason=e.to_jsons()[:100])

    except WebSocketDisconnect:
        ...
