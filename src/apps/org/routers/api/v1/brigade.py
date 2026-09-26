from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.org.dto.internal.brigade import (
    BrigadeDangerZoneItemDTO,
    BrigadeDTO,
    BrigadeRepairStateDTO,
    BrigadesKPIDTO,
)
from apps.org.dto.queries.brigade import (
    GetBrigadeRepairStateQuery,
    GetBrigadesKPIQuery,
    ListBrigadesByNGDUIdQuery,
    ListBrigadesInDangerZoneQuery,
)
from apps.org.dto.responses.brigade import (
    BrigadeRepairStateResponseDTO,
    BrigadesInDangerZoneResponseDTO,
    BrigadesKPIResponseDTO,
    ListBrigadesResponseDTO,
)
from apps.org.repositories import UniqueBrigadeRepository
from apps.org.use_cases.get_brigade_repair_state import GetBrigadeRepairStateUseCase
from apps.org.use_cases.get_brigades_kpi import GetBrigadesKPIUseCase
from apps.org.use_cases.list_brigades_by_ngdu_id import ListBrigadesByNGDUIdUseCase
from apps.org.use_cases.list_brigades_in_danger_zone import (
    ListBrigadesInDangerZoneUseCase,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.repositories import WellRepository
from apps.wells.repositories.spo import SPORepository
from shared.dependencies.db import get_app_session, get_cm_session
from shared.dto.api import AppResponse
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository

router = APIRouter(prefix="/brigades", tags=["brigades"])


@router.get("/matrix", response_model=AppResponse[list[BrigadeDTO]])
async def list_brigades_by_ngdu_id(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    ngdu_id: Annotated[int, Query(ge=1, description="NGDU ID (filter)")],
) -> ListBrigadesResponseDTO:
    use_case = ListBrigadesByNGDUIdUseCase(
        spo_repository=SPORepository(session=app_session),
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        well_repository=WellRepository(session=app_session),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
    )
    brigades = await use_case.execute(
        ListBrigadesByNGDUIdQuery(ngdu_id=ngdu_id),
    )
    return ListBrigadesResponseDTO(data=brigades)


@router.get("/kpi", response_model=AppResponse[BrigadesKPIDTO])
async def get_brigades_kpi(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    ngdu_id: Annotated[int, Query(ge=1, description="Local Org.id of the NGDU")],
) -> BrigadesKPIResponseDTO:
    use_case = GetBrigadesKPIUseCase(
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
    )
    kpi = await use_case.execute(GetBrigadesKPIQuery(ngdu_id=ngdu_id))
    return BrigadesKPIResponseDTO(data=kpi)


@router.get(
    "/danger-zone",
    response_model=AppResponse[list[BrigadeDangerZoneItemDTO]],
)
async def list_brigades_in_danger_zone(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    ngdu_id: Annotated[int, Query(ge=1, description="Local Org.id of the NGDU")],
) -> BrigadesInDangerZoneResponseDTO:
    use_case = ListBrigadesInDangerZoneUseCase(
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
    )
    items = await use_case.execute(
        ListBrigadesInDangerZoneQuery(ngdu_id=ngdu_id),
    )
    return BrigadesInDangerZoneResponseDTO(data=items)


@router.get(
    "/{brigade_id}/repair-state",
    response_model=AppResponse[BrigadeRepairStateDTO],
)
async def get_brigade_repair_state(
    app_session: Annotated[AsyncSession, Depends(get_app_session)],
    cm_session: Annotated[AsyncSession, Depends(get_cm_session)],
    brigade_id: Annotated[
        int,
        Path(ge=1, description="org_unique_brigade.id"),
    ],
) -> BrigadeRepairStateResponseDTO:
    use_case = GetBrigadeRepairStateUseCase(
        unique_brigade_repository=UniqueBrigadeRepository(session=app_session),
        repair_brigade_repository=RepairBrigadeRepository(session=app_session),
        repair_repository=RepairRepository(session=app_session),
        well_repository=WellRepository(session=app_session),
        cm_brigade_repository=CMBrigadeRepository(session=cm_session),
        cm_brigade_error_screen_repository=CMBrigadeErrorScreenRepository(
            session=cm_session,
        ),
    )
    state = await use_case.execute(
        GetBrigadeRepairStateQuery(brigade_id=brigade_id),
    )
    return BrigadeRepairStateResponseDTO(data=state)
