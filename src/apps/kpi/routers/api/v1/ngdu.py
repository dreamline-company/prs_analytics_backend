from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
)
from apps.detectors.services import WellIncidentStatusService
from apps.kpi.dto.internal.ngdu_summary import NgduSummaryDTO
from apps.kpi.dto.queries.ngdu_summary import GetNgduSummaryQuery
from apps.kpi.dto.responses.ngdu_summary import NgduSummaryResponseDTO
from apps.kpi.use_cases.get_ngdu_summary import GetNgduSummaryUseCase
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_org import WellOrgRepository
from apps.wells.services import NGDUWellsService
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/ngdu", tags=["kpi"])


@router.get("/summary", response_model=AppResponse[NgduSummaryDTO])
async def get_ngdu_summary(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: Annotated[
        int | None,
        Query(
            ge=1,
            description="Optional local Org.id of the NGDU; omit for all NGDUs",
        ),
    ] = None,
    oil_field_id: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Optional oil_fields.id (see GET /org/v1/oil-fields); narrows to "
                "the oil field's wells and implies its NGDU; must match ngdu_id "
                "if both are set"
            ),
        ),
    ] = None,
) -> NgduSummaryResponseDTO:
    use_case = GetNgduSummaryUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
        ),
        org_repository=OrgRepository(session=app_session),
        telemetry_repository=TelemetryRepository(session=app_session),
        tech_regime_repository=TechRegimeRepository(session=app_session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=app_session),
        well_incident_status_service=WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session=app_session),
            detector_repository=DetectorRepository(session=app_session),
        ),
    )
    summary = await use_case.execute(
        GetNgduSummaryQuery(ngdu_id=ngdu_id, oil_field_id=oil_field_id),
    )
    return NgduSummaryResponseDTO(data=summary)
