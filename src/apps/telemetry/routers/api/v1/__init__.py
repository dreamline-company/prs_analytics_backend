from fastapi import APIRouter

from .sdmo import router as sdmo_router
from .tech_regime import router as tech_regime_router
from .telemetry import router as telemetry_router

router = APIRouter(prefix="/v1")

router.include_router(tech_regime_router)
router.include_router(telemetry_router)
router.include_router(sdmo_router)
