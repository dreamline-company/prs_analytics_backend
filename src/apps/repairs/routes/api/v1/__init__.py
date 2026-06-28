from fastapi import APIRouter

from apps.repairs.routes.api.v1.repair import router as repair_router
from apps.repairs.routes.api.v1.summary import router as summary_router

router = APIRouter(prefix="/v1")

router.include_router(repair_router)
router.include_router(summary_router)
