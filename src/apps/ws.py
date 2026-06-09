from fastapi import APIRouter

from core.settings import get_settings

from .ai_assistant.routers.ws import ws_router as ai_ws_router
from .kpi.routers.ws import ws_router as kpi_ws_router

settings = get_settings()
server_ws_router = APIRouter(prefix=settings.WS_PREFIX)
server_ws_router.include_router(kpi_ws_router)
server_ws_router.include_router(ai_ws_router)
