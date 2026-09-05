from fastapi import APIRouter

from .brigade import router as brigade_router
from .ngdu import router as ngdu_router
from .oil_field import router as oil_field_router

router = APIRouter(prefix="/v1")
router.include_router(ngdu_router)
router.include_router(brigade_router)
router.include_router(oil_field_router)
