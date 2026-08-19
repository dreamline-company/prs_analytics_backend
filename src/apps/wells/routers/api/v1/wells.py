from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.internal.well_matrix import WellMatrixItemDTO
from apps.wells.dto.internal.well_matrix_incidents import WellMatrixIncidentDTO
from apps.wells.dto.queries.well import (
    GetWellCardQuery,
    GetWellMatrixIncidentsQuery,
    GetWellsMatrixQuery,
    SearchWellsByNameQuery,
)
from apps.wells.dto.responses.well import (
    SearchWellsResponseDTO,
    WellCardResponseDTO,
    WellMatrixIncidentsResponseDTO,
    WellsMatrixResponseDTO,
)
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.services import NGDUWellsService
from apps.wells.use_cases.get_well_card import GetWellCardUseCase
from apps.wells.use_cases.get_well_matrix_incidents import (
    GetWellMatrixIncidentsUseCase,
)
from apps.wells.use_cases.get_wells_matrix import GetWellsMatrixUseCase
from apps.wells.use_cases.search_wells_by_name import SearchWellsByNameUseCase
from shared.dependencies.db import get_abai_session, get_app_session, get_cm_session
from shared.dto.api import AppResponse
from shared.integrations.abai.repositories.well_orgs import ABAIWellOrgRepository
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

router = APIRouter(prefix="/wells", tags=["wells"])


@router.get("/search", response_model=AppResponse[list[WellShortDTO]])
async def search_wells_by_name(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    name: Annotated[
        str,
        Query(min_length=1, max_length=15, description="Well name substring"),
    ],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> SearchWellsResponseDTO:
    use_case = SearchWellsByNameUseCase(
        well_repository=WellRepository(session=session),
    )
    wells = await use_case.execute(
        SearchWellsByNameQuery(name=name, limit=limit),
    )
    return SearchWellsResponseDTO(data=wells)


@router.get("/matrix", response_model=AppResponse[list[WellMatrixItemDTO]])
async def get_wells_matrix(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    abai_session: Annotated[AsyncSession, Depends(get_abai_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    ngdu_id: Annotated[
        int,
        Query(ge=1, description="Local Org.id of the NGDU to filter wells by"),
    ],
) -> WellsMatrixResponseDTO:
    use_case = GetWellsMatrixUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            abai_well_org_repository=ABAIWellOrgRepository(session=abai_session),
        ),
        repair_repository=RepairRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
    )
    matrix = await use_case.execute(GetWellsMatrixQuery(ngdu_id=ngdu_id))
    return WellsMatrixResponseDTO(data=matrix)


@router.get(
    "/matrix/incidents",
    response_model=AppResponse[list[WellMatrixIncidentDTO]],
)
async def get_well_matrix_incidents(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    abai_session: Annotated[AsyncSession, Depends(get_abai_session)],
    ngdu_id: Annotated[
        int,
        Query(ge=1, description="Local Org.id of the NGDU to filter wells by"),
    ],
) -> WellMatrixIncidentsResponseDTO:
    use_case = GetWellMatrixIncidentsUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            abai_well_org_repository=ABAIWellOrgRepository(session=abai_session),
        ),
        well_expl_repository=WellExplRepository(session=app_session),
    )
    incidents = await use_case.execute(GetWellMatrixIncidentsQuery(ngdu_id=ngdu_id))
    return WellMatrixIncidentsResponseDTO(data=incidents)


@router.get("/{well_id}/card", response_model=AppResponse[WellCardDTO])
async def get_well_card(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Path(ge=1, description="Well ID (wells_well.id)")],
) -> WellCardResponseDTO:
    use_case = GetWellCardUseCase(
        well_repository=WellRepository(session=session),
        telemetry_repository=TelemetryRepository(session=session),
        tech_regime_repository=TechRegimeRepository(session=session),
        sdmo_station_repository=SdmoStationRepository(session=session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
        well_status_history_repository=WellStatusHistoryRepository(session=session),
    )
    card = await use_case.execute(GetWellCardQuery(well_id=well_id))
    return WellCardResponseDTO(data=card)
