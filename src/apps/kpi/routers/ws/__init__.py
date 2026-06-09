from fastapi import APIRouter

from .v1 import ws_v1_router as ws_v1

ws_router = APIRouter(prefix="/kpi")

ws_router.include_router(ws_v1)
