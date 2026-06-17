from fastapi import APIRouter

from apps.repairs.routes.api.v1.summary import router as summary_router

router = APIRouter(prefix="/v1")

router.include_router(summary_router)
