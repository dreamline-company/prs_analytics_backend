import asyncio

from fastapi import APIRouter
from starlette.websockets import WebSocket, WebSocketDisconnect

from apps.kpi.dto.queries.main_kpi import GetMainKpiStatusUpdatesCommand
from apps.kpi.dto.responses.main_kpi import MainKPIStatusResponse
from apps.kpi.use_cases.get_main_kpi_status_updates import GetMainKpiNewStatusUseCase

ws_router = APIRouter(prefix="/main-kpi")


@ws_router.websocket("/")
async def kpi_websocket(ws: WebSocket) -> None:
    await ws.accept()
    try:
        use_case = GetMainKpiNewStatusUseCase()
        while True:
            result = await use_case.execute(
                query=GetMainKpiStatusUpdatesCommand(),
            )
            if result.is_new:
                response = MainKPIStatusResponse(data=result.data)
                await ws.send_json(response.model_dump(mode="json"))
            await asyncio.sleep(3)

    except WebSocketDisconnect:
        ...
