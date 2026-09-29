"""Контур компенсации: потери стоящих скважин и доноры, которые их закрывают.

Согласование пар технологом реализовано, но эндпоинт не публикуется, пока
процесс не утверждён. У Кайнармунайгаза в контур входит только месторождение VMB.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.dto.internal.compensation import (
    CompensationContourDTO,
    CompensationDonorsDTO,
    CompensationLossesDTO,
    CompensationRecommendationsDTO,
)
from apps.compensation.dto.queries.compensation import (
    DonorStatus,
    GetCompensationContourQuery,
    ListCompensationDonorsQuery,
    ListCompensationQuery,
    ListCompensationRecommendationsQuery,
)
from apps.compensation.dto.responses.compensation import (
    CompensationContourResponseDTO,
    CompensationDonorsResponseDTO,
    CompensationLossesResponseDTO,
    CompensationRecommendationsResponseDTO,
)
from apps.compensation.use_cases.get_contour import GetCompensationContourUseCase
from apps.compensation.use_cases.list_donors import ListCompensationDonorsUseCase
from apps.compensation.use_cases.list_losses import ListCompensationLossesUseCase
from apps.compensation.use_cases.list_recommendations import (
    ListCompensationRecommendationsUseCase,
)
from shared.dependencies.db import get_app_session
from shared.dto.api import AppResponse

router = APIRouter(tags=["compensation"])

NgduId = Annotated[int, Query(ge=1, description="Local Org.id of the NGDU")]
OilFieldId = Annotated[
    int | None,
    Query(
        ge=1,
        description=(
            "Optional oil_fields.id (GET /org/v1/oil-fields); must belong to ngdu_id"
        ),
    ),
]


@router.get("/contour", response_model=AppResponse[CompensationContourDTO])
async def get_compensation_contour(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    well_id: Annotated[int, Query(ge=1, description="Well ID (wells_well.id)")],
) -> CompensationContourResponseDTO:
    contour = await GetCompensationContourUseCase(session).execute(
        GetCompensationContourQuery(well_id=well_id),
    )
    return CompensationContourResponseDTO(data=contour)


@router.get(
    "/recommendations",
    response_model=AppResponse[CompensationRecommendationsDTO],
)
async def get_compensation_recommendations(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: NgduId,
    oil_field_id: OilFieldId = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 3,
) -> CompensationRecommendationsResponseDTO:
    recommendations = await ListCompensationRecommendationsUseCase(session).execute(
        ListCompensationRecommendationsQuery(
            ngdu_id=ngdu_id,
            oil_field_id=oil_field_id,
            limit=limit,
        ),
    )
    return CompensationRecommendationsResponseDTO(data=recommendations)


@router.get("/donors", response_model=AppResponse[CompensationDonorsDTO])
async def get_compensation_donors(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: NgduId,
    oil_field_id: OilFieldId = None,
    status: Annotated[
        DonorStatus | None,
        Query(description="pending | accepted | applied | rejected | reserve"),
    ] = None,
) -> CompensationDonorsResponseDTO:
    donors = await ListCompensationDonorsUseCase(session).execute(
        ListCompensationDonorsQuery(
            ngdu_id=ngdu_id,
            oil_field_id=oil_field_id,
            status=status,
        ),
    )
    return CompensationDonorsResponseDTO(data=donors)


@router.get("/losses", response_model=AppResponse[CompensationLossesDTO])
async def get_compensation_losses(
    session: Annotated[AsyncSession, Depends(get_app_session)],
    ngdu_id: NgduId,
    oil_field_id: OilFieldId = None,
) -> CompensationLossesResponseDTO:
    losses = await ListCompensationLossesUseCase(session).execute(
        ListCompensationQuery(ngdu_id=ngdu_id, oil_field_id=oil_field_id),
    )
    return CompensationLossesResponseDTO(data=losses)
