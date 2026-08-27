from fastapi import APIRouter

from .incidents import router as incidents_router

router = APIRouter(prefix="/v1")

router.include_router(incidents_router)
