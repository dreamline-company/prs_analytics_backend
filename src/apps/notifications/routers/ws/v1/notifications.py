"""WS уведомлений: ``/prs-analytics/ws/notifications/v1``.

Клиент первым кадром шлёт ``{"type":"hello","after_id":N|null}`` и получает
``snapshot``, дальше — ``notification`` / ``viewed`` / ``ping`` от хаба
воркера. Прочие кадры клиента игнорируются. Кадр не JSON или кривой
``hello`` — сокет закрывается с кодом 1007.
"""

import asyncio
import json
from contextlib import suppress
from typing import Any

from fastapi import APIRouter
from pydantic import ValidationError
from starlette import status
from starlette.websockets import WebSocket

from apps.notifications.dto.requests.ws import HelloFrameDTO
from apps.notifications.services.hub import HubClient, NotificationHub
from core import get_logger
from shared.errors import WSError

logger = get_logger(__name__)
ws_router = APIRouter()


class BadNotificationFrameError(WSError):
    message = "Bad notification frame."
    close_code = status.WS_1007_INVALID_FRAME_PAYLOAD_DATA


def parse_client_frame(text: str) -> HelloFrameDTO | None:
    """``hello`` → DTO, прочие кадры → ``None``; мусор — ошибка."""
    try:
        data: Any = json.loads(text)
    except json.JSONDecodeError as e:
        raise BadNotificationFrameError(details={"json": str(e)}) from e
    # Не объект («"ping"», «[1]») и объект без type=hello — keepalive и прочее,
    # их игнорируем.
    if not isinstance(data, dict) or data.get("type") != "hello":
        return None
    try:
        return HelloFrameDTO.model_validate(data)
    except ValidationError as e:
        raise BadNotificationFrameError(details={"validation": str(e)}) from e


async def _read_loop(ws: WebSocket, hub: NotificationHub, client: HubClient) -> None:
    """Кадры клиента до отключения."""
    while True:
        message = await ws.receive()
        if message["type"] == "websocket.disconnect":
            return
        text = message.get("text")
        if text is None and message.get("bytes") is not None:
            text = message["bytes"].decode("utf-8", errors="replace")
        if text is None:
            continue
        hello = parse_client_frame(text)
        if hello is not None:
            await hub.attach(client, hello.after_id)


async def _close(ws: WebSocket, code: int, reason: str = "") -> None:
    with suppress(Exception):
        await ws.close(code=code, reason=reason)


@ws_router.websocket("/v1")
@ws_router.websocket("/v1/")
async def notifications_websocket(ws: WebSocket) -> None:
    hub: NotificationHub = ws.app.state.notification_hub
    await ws.accept()
    client = HubClient(ws)
    # Писатель при сбое отправки сам закрывает сокет — чтение ниже получает
    # отключение и выходит.
    writer = asyncio.create_task(client.run())
    try:
        await _read_loop(ws, hub, client)
    except BadNotificationFrameError as e:
        logger.debug("Bad notification frame: %s", e.to_jsons())
        await _close(ws, e.close_code, e.to_jsons()[:100])
    except Exception:
        # Снимок не собрался (БД недоступна и т. п.): клиент переподключится.
        logger.exception("Notifications socket failed")
        await _close(ws, status.WS_1011_INTERNAL_ERROR)
    finally:
        hub.detach(client)
        writer.cancel()
