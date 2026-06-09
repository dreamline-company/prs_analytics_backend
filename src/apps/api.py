from fastapi import APIRouter

from core.settings import get_settings

from .wells.routers.api import router as wells_router_api

settings = get_settings()
server_router = APIRouter(prefix=settings.API_PREFIX)
server_router.include_router(wells_router_api)
