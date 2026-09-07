from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
)
from apps.detectors.services import WellIncidentStatusService
from apps.files.repositories.file import FileRepository
from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.services import CurrentRepairService
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services import WellRatesService
from apps.wells.dto.internal.dynamogram import WellDynamogramDTO
from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.internal.well_coords import WellCoordMapPointDTO
from apps.wells.dto.internal.well_matrix import WellMatrixItemDTO
from apps.wells.dto.internal.well_matrix_incidents import WellMatrixIncidentDTO
from apps.wells.dto.queries.well import (
    GetWellCardQuery,
    GetWellCoordsQuery,
    GetWellMatrixIncidentsQuery,
    GetWellsMatrixQuery,
    ListWellDynamogramsQuery,
    SearchWellsByNameQuery,
)
from apps.wells.dto.responses.well import (
    SearchWellsResponseDTO,
    WellCardResponseDTO,
    WellCoordsResponseDTO,
    WellDynamogramsResponseDTO,
    WellMatrixIncidentsResponseDTO,
    WellsMatrixResponseDTO,
)
from apps.wells.repositories.coords import WellCoordRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.repositories.well_org import WellOrgRepository
from apps.wells.services import CoordPointService, NGDUWellsService
from apps.wells.use_cases.get_well_card import GetWellCardUseCase
from apps.wells.use_cases.get_well_coords import GetWellCoordsUseCase
from apps.wells.use_cases.get_well_matrix_incidents import (
    GetWellMatrixIncidentsUseCase,
)
from apps.wells.use_cases.get_wells_matrix import GetWellsMatrixUseCase
from apps.wells.use_cases.list_dynamograms_by_well_id import (
    ListDynamogramsByWellIdUseCase,
)
from apps.wells.use_cases.search_wells_by_name import SearchWellsByNameUseCase
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_aioboto_presign_client_factory,
    get_app_session,
    get_cm_session,
)
from shared.dto.api import AppResponse
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

settings = get_settings()
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
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
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
    ngdu_id: Annotated[
        int,
        Query(ge=1, description="Local Org.id of the NGDU to filter wells by"),
    ],
    oil_field_id: Annotated[
        int | None,
        Query(
            ge=1,
            description=(
                "Optional oil_fields.id (see GET /org/v1/oil-fields?ngdu_id=...) "
                "to keep only wells of that oil field; must belong to ngdu_id"
            ),
        ),
    ] = None,
) -> WellMatrixIncidentsResponseDTO:
    use_case = GetWellMatrixIncidentsUseCase(
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
        ),
        well_expl_repository=WellExplRepository(session=app_session),
        well_incident_status_service=WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session=app_session),
            detector_repository=DetectorRepository(session=app_session),
        ),
        current_repair_service=CurrentRepairService(
            repair_repository=RepairRepository(session=app_session),
        ),
        well_rates_service=WellRatesService(
            telemetry_repository=TelemetryRepository(session=app_session),
            tech_regime_repository=TechRegimeRepository(session=app_session),
        ),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=app_session),
    )
    incidents = await use_case.execute(
        GetWellMatrixIncidentsQuery(ngdu_id=ngdu_id, oil_field_id=oil_field_id),
    )
    return WellMatrixIncidentsResponseDTO(data=incidents)


@router.get("/coords", response_model=AppResponse[list[WellCoordMapPointDTO]])
async def get_well_coords(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: Annotated[
        int | None,
        Query(ge=1, description="Optional local Org.id of the NGDU to filter by"),
    ] = None,
) -> WellCoordsResponseDTO:
    use_case = GetWellCoordsUseCase(
        well_coord_repository=WellCoordRepository(session=app_session),
        ngdu_wells_service=NGDUWellsService(
            org_repository=OrgRepository(session=app_session),
            well_repository=WellRepository(session=app_session),
            well_org_repository=WellOrgRepository(session=app_session),
            oil_field_repository=OilFieldRepository(session=app_session),
        ),
    )
    points = await use_case.execute(GetWellCoordsQuery(ngdu_id=ngdu_id))
    return WellCoordsResponseDTO(data=points)


@router.get("/{well_id}/card", response_model=AppResponse[WellCardDTO])
async def get_well_card(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Path(ge=1, description="Well ID (wells_well.id)")],
) -> WellCardResponseDTO:
    use_case = GetWellCardUseCase(
        well_repository=WellRepository(session=session),
        well_rates_service=WellRatesService(
            telemetry_repository=TelemetryRepository(session=session),
            tech_regime_repository=TechRegimeRepository(session=session),
        ),
        sdmo_station_repository=SdmoStationRepository(session=session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
        well_status_history_repository=WellStatusHistoryRepository(session=session),
        coord_point_service=CoordPointService(
            well_coord_repository=WellCoordRepository(session=session),
        ),
        well_incident_status_service=WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session=session),
            detector_repository=DetectorRepository(session=session),
        ),
        current_repair_service=CurrentRepairService(
            repair_repository=RepairRepository(session=session),
        ),
    )
    card = await use_case.execute(GetWellCardQuery(well_id=well_id))
    return WellCardResponseDTO(data=card)


@router.get(
    "/{well_id}/dynamograms",
    response_model=AppResponse[list[WellDynamogramDTO]],
)
async def get_well_dynamograms(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Path(ge=1, description="Well ID (wells_well.id)")],
    expires_in: Annotated[
        int,
        Query(
            ge=60,
            le=7 * 24 * 3600,
            description="Presigned URL lifetime, seconds (60..604800). Default 1h.",
        ),
    ] = 3600,
) -> WellDynamogramsResponseDTO:
    # Бакет тот же, что у /files/{file_id}: все загрузки сейчас ложатся туда.
    storage = AiobotoFileStorage(
        bucket_name=settings.PRS_REPAIRS_BUCKET_NAME,
        client_factory=get_aioboto_client_factory(),
        presign_client_factory=get_aioboto_presign_client_factory(),
    )
    use_case = ListDynamogramsByWellIdUseCase(
        well_repository=WellRepository(session=session),
        dynamogram_repository=DynamogramRepository(session=session),
        file_repository=FileRepository(session=session),
        storage=storage,
    )
    dynamograms = await use_case.execute(
        ListWellDynamogramsQuery(well_id=well_id, expires_in=expires_in),
    )
    return WellDynamogramsResponseDTO(data=dynamograms)
