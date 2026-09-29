from fastapi import APIRouter

from .compensation import router as compensation_router

router = APIRouter(prefix="/v1")
router.include_router(compensation_router)
