from fastapi import APIRouter

from .detection import router as detection_router

router = APIRouter(prefix="/v1")

router.include_router(detection_router)
