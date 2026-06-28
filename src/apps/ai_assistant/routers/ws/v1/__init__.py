from fastapi import APIRouter

from .prs_assistant_chat import ws_router

ws_v1_router = APIRouter(prefix="/v1")
ws_v1_router.include_router(ws_router)
