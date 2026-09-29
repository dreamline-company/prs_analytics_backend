from typing import Annotated

from fastapi import APIRouter, Depends, Path
from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.repositories.file import FileRepository
from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.repairs.dto.internal.analytics_view import RepairAnalyticsViewDTO
from apps.repairs.dto.internal.kpi import RepairKPIViewDTO
from apps.repairs.dto.internal.timeline import RepairTimelineDTO
from apps.repairs.dto.queries.analytics_view import GetRepairAnalyticsViewQuery
from apps.repairs.dto.queries.kpi import GetRepairKPIQuery
from apps.repairs.dto.queries.timeline import GetRepairTimelineQuery
from apps.repairs.dto.responses.analytics_view import RepairAnalyticsViewResponseDTO
from apps.repairs.dto.responses.kpi import RepairKPIResponseDTO
from apps.repairs.dto.responses.timeline import RepairTimelineResponseDTO
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.kpi import RepairKPIRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.repositories.transport import RepairTransportRepository
from apps.repairs.use_cases.get_repair_analytics_view import (
    GetRepairAnalyticsViewUseCase,
)
from apps.repairs.use_cases.get_repair_kpi import GetRepairKPIUseCase
from apps.repairs.use_cases.get_repair_timeline import GetRepairTimelineUseCase
from apps.wells.repositories import WellRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_app_session,
    get_cm_session,
)
from shared.dto.api import AppResponse
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

router = APIRouter(prefix="/analytics", tags=["analytics"])
settings = get_settings()


@router.get(
    "/by-repair/{repair_id}",
    response_model=AppResponse[RepairAnalyticsViewDTO],
)
async def get_repair_analytics_view(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    repair_id: Annotated[int, Path(ge=1, description="Repair ID")],
) -> RepairAnalyticsViewResponseDTO:
    use_case = GetRepairAnalyticsViewUseCase(
        wells_repository=WellRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        analytics_repository=RepairAnalyticsRepository(session=app_session),
        analytics_dynamogram_repository=RepairAnalyticsDynamogramRepository(
            session=app_session,
        ),
        analytics_spo_repository=RepairAnalyticsSPORepository(session=app_session),
        dynamogram_repository=DynamogramRepository(session=app_session),
        spo_repository=SPORepository(session=app_session),
        dynamogram_ai_repository=RepairDynamogramAIResultRepository(
            session=app_session,
        ),
        spo_ai_repository=RepairSPOAIResultRepository(session=app_session),
        overall_ai_repository=RepairAIAnalysisRepository(session=app_session),
        transport_repository=RepairTransportRepository(session=app_session),
        file_repository=FileRepository(session=app_session),
        storage=AiobotoFileStorage(
            bucket_name=settings.S3_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
        ),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
        cm_media_url_header=settings.CM_MEDIA_URL_HEADER,
    )
    view = await use_case.execute(GetRepairAnalyticsViewQuery(repair_id=repair_id))
    return RepairAnalyticsViewResponseDTO(data=view)


@router.get(
    "/by-repair/{repair_id}/timeline",
    response_model=AppResponse[RepairTimelineDTO],
)
async def get_repair_timeline(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    repair_id: Annotated[int, Path(ge=1, description="Repair ID")],
) -> RepairTimelineResponseDTO:
    use_case = GetRepairTimelineUseCase(
        repair_repository=RepairRepository(session=app_session),
        analytics_repository=RepairAnalyticsRepository(session=app_session),
        analytics_dynamogram_repository=RepairAnalyticsDynamogramRepository(
            session=app_session,
        ),
        dynamogram_repository=DynamogramRepository(session=app_session),
        spo_repository=SPORepository(session=app_session),
        doc_repository=RepairDocRepository(session=app_session),
        summary_repository=RepairSummaryRepository(session=app_session),
        file_repository=FileRepository(session=app_session),
    )
    timeline = await use_case.execute(GetRepairTimelineQuery(repair_id=repair_id))
    return RepairTimelineResponseDTO(data=timeline)


@router.get(
    "/by-repair/{repair_id}/kpi",
    response_model=AppResponse[RepairKPIViewDTO],
)
async def get_repair_kpi(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    repair_id: Annotated[int, Path(ge=1, description="Repair ID")],
) -> RepairKPIResponseDTO:
    use_case = GetRepairKPIUseCase(
        analytics_repository=RepairAnalyticsRepository(session=app_session),
        kpi_repository=RepairKPIRepository(session=app_session),
    )
    kpi = await use_case.execute(GetRepairKPIQuery(repair_id=repair_id))
    return RepairKPIResponseDTO(data=kpi)
