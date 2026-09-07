from fastapi import APIRouter

from .ngdu import router as ngdu_router

router = APIRouter(prefix="/v1")
router.include_router(ngdu_router)
