from fastapi import APIRouter

from .main_kpi import ws_router

ws_v1_router = APIRouter(prefix="/v1")
ws_v1_router.include_router(ws_router)
