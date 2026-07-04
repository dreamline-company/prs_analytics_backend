from fastapi import APIRouter

from .file import router as file_router

router = APIRouter(prefix="/v1")
router.include_router(file_router)
