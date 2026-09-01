from fastapi import APIRouter

from .conclusions import router as conclusions_router
from .incidents import router as incidents_router

router = APIRouter(prefix="/v1")

router.include_router(incidents_router)
router.include_router(conclusions_router)
