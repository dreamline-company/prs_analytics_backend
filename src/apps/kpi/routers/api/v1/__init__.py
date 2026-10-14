from fastapi import APIRouter

from .mrp import router as mrp_router
from .ngdu import router as ngdu_router

router = APIRouter(prefix="/v1")
router.include_router(ngdu_router)
router.include_router(mrp_router)
