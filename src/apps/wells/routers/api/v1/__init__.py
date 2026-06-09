from fastapi import APIRouter

from .wells_twin import router as wells_twin_router

router = APIRouter(prefix="/v1")
router.include_router(wells_twin_router)
