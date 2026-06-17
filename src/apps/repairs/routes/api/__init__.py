from fastapi import APIRouter

from apps.repairs.routes.api.v1 import router as v1_router

router = APIRouter(prefix="/repairs")

router.include_router(v1_router)
