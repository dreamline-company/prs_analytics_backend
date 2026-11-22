from fastapi import APIRouter

from .v1 import ws_router as ws_v1_router

# /prs-analytics/ws/notifications/v1
ws_router = APIRouter(prefix="/notifications")

ws_router.include_router(ws_v1_router)
