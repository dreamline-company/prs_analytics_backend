from fastapi import APIRouter

from .notifications import router as notifications_router

router = APIRouter(prefix="/v1")
router.include_router(notifications_router)
