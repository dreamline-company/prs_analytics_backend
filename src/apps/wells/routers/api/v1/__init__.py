from fastapi import APIRouter

from .wells import router as wells_router
from .wells_twin import router as wells_twin_router

router = APIRouter(prefix="/v1")
router.include_router(wells_router)
