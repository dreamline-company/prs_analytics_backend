from fastapi import APIRouter

from core.settings import get_settings

from .detectors.rod_breaks.routers.api import router as rod_breaks_router_api
from .files.routers.api import router as files_router_api
from .org.routers.api import router as org_router_api
from .repairs.routes.api import router as repairs_router_api
from .telemetry.routers.api import router as telemetry_router_api
from .wells.routers.api import router as wells_router_api

settings = get_settings()
server_router = APIRouter(prefix=settings.API_PREFIX)
server_router.include_router(wells_router_api)
server_router.include_router(repairs_router_api)
server_router.include_router(telemetry_router_api)
server_router.include_router(org_router_api)
server_router.include_router(files_router_api)
server_router.include_router(rod_breaks_router_api)
