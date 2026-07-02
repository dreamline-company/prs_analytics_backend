from fastapi import APIRouter

from .brigade import router as brigade_router
from .ngdu import router as ngdu_router

router = APIRouter(prefix="/v1")
router.include_router(ngdu_router)
router.include_router(brigade_router)
