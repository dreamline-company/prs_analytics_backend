from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.kpi.dto.internal.mrp import MrpDynamicsDTO, MrpSummaryDTO
from apps.kpi.dto.queries.mrp import GetMrpDynamicsQuery, GetMrpSummaryQuery
from apps.kpi.dto.responses.mrp import (
    MrpDynamicsResponseDTO,
    MrpSummaryResponseDTO,
)
from apps.kpi.use_cases.get_mrp_dynamics import GetMrpDynamicsUseCase
from apps.kpi.use_cases.get_mrp_summary import GetMrpSummaryUseCase
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.services.mrp_inputs import MrpInputsService
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.repositories.well_org import WellOrgRepository
from apps.wells.repositories.well_status import WellStatusRepository
from apps.wells.services import NGDUWellsService
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(prefix="/mrp", tags=["kpi"])


@router.get("/summary", response_model=AppResponse[MrpSummaryDTO])
async def get_mrp_summary(  # noqa: PLR0913, PLR0917
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
                "the oil field's wells and implies its NGDU"
            ),
        ),
    ] = None,
    end_month: Annotated[
        str | None,
        Query(
            pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
            description="Last month of the window, YYYY-MM; default: previous month",
        ),
    ] = None,
    months: Annotated[
        int,
        Query(ge=1, le=36, description="Window length in months (rolling year: 12)"),
    ] = 12,
    crf_min_repairs: Annotated[
        int,
        Query(ge=1, description="CRF threshold: N or more non-GTM repairs"),
    ] = 3,
) -> MrpSummaryResponseDTO:
    """МРП по методике КМГ (с ГТМ и без) и часто ремонтируемый фонд."""
    use_case = GetMrpSummaryUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
        ),
        mrp_inputs_service=MrpInputsService(
            repair_repository=RepairRepository(session=app_session),
            status_repository=WellStatusRepository(session=app_session),
            expl_repository=WellExplRepository(session=app_session),
        ),
    )
    summary = await use_case.execute(
        GetMrpSummaryQuery(
            ngdu_id=ngdu_id,
            oil_field_id=oil_field_id,
            end_month=end_month,
            months=months,
            crf_min_repairs=crf_min_repairs,
        ),
    )
    return MrpSummaryResponseDTO(data=summary)


@router.get("/dynamics", response_model=AppResponse[MrpDynamicsDTO])
async def get_mrp_dynamics(  # noqa: PLR0913, PLR0917
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
                "the oil field's wells and implies its NGDU"
            ),
        ),
    ] = None,
    end_month: Annotated[
        str | None,
        Query(
            pattern=r"^\d{4}-(0[1-9]|1[0-2])$",
            description="Last month of the series, YYYY-MM; default: previous month",
        ),
    ] = None,
    points: Annotated[
        int,
        Query(ge=1, le=36, description="Number of months in the series"),
    ] = 12,
    window_months: Annotated[
        int,
        Query(ge=1, le=24, description="Rolling window length in months (KMG: 12)"),
    ] = 12,
) -> MrpDynamicsResponseDTO:
    """Динамика МРП по методике КМГ: показатели месяца и скользящего периода."""
    use_case = GetMrpDynamicsUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
        ),
        mrp_inputs_service=MrpInputsService(
            repair_repository=RepairRepository(session=app_session),
            status_repository=WellStatusRepository(session=app_session),
            expl_repository=WellExplRepository(session=app_session),
        ),
    )
    dynamics = await use_case.execute(
        GetMrpDynamicsQuery(
            ngdu_id=ngdu_id,
            oil_field_id=oil_field_id,
            end_month=end_month,
            points=points,
            window_months=window_months,
        ),
    )
    return MrpDynamicsResponseDTO(data=dynamics)
