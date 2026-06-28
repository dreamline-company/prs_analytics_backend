import json
from typing import Annotated

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
from apps.ai_assistant.dto.requests.chat_assistant import AskChatAssistantRequestDTO
from apps.ai_assistant.dto.responses.chat_assistant import ChatAssistantResponseDTO
from apps.ai_assistant.use_cases.ask_chat_assistant import AskChatAssistantUseCase
from core import get_logger
from shared.ai.llm.messages import HumanMessageDTO
from shared.errors import WSError

logger = get_logger(__name__)
ws_router = APIRouter(prefix="/prs")


class BadRequestDataError(WSError):
    message = "Bad Request Data."
    close_code = status.WS_1007_INVALID_FRAME_PAYLOAD_DATA


@ws_router.websocket("/chat/{chat_id}")
async def assistant_chat_websocket(
    ws: WebSocket,
    chat_id: Annotated[str, Path(description="Chat UUID")],
) -> None:
    await ws.accept()
    try:
        use_case = AskChatAssistantUseCase(
            agent=chat_assistant_agent,
            config_factory=get_agent_config,
        )

        while True:
            data = await ws.receive_json()
            try:
                if isinstance(data, str):
                    data = json.loads(data)
                request = AskChatAssistantRequestDTO(**data)
            except ValidationError as ve:
                raise BadRequestDataError(details={"validation": str(ve)}) from ve
            except json.decoder.JSONDecodeError as e:
                raise BadRequestDataError(details={"json": e}) from e
            result = await use_case.execute(
                command=AskChatAssistantCommand(
                    chat_id=chat_id,
                    message=HumanMessageDTO(content=request.message),
                ),
            )
            await ws.send_json(
                ChatAssistantResponseDTO(
                    chat_id=chat_id,
                    text=result.content,
                ).model_dump(mode="json"),
            )

    except BadRequestDataError as e:
        logger.debug("Bad Request Data: %s", e.to_jsons())
        await ws.close(code=e.close_code, reason=e.to_jsons()[:100])

    except WebSocketDisconnect:
        ...
