from fastapi import APIRouter

from core.settings import get_settings

from .repairs.routes.api import router as repairs_router_api

settings = get_settings()
server_router = APIRouter(prefix=settings.API_PREFIX)
# server_router.include_router(wells_router_api)
server_router.include_router(repairs_router_api)
