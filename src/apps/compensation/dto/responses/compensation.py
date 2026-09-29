from apps.compensation.dto.internal.compensation import (
    CompensationContourDTO,
    CompensationDonorsDTO,
    CompensationLossesDTO,
    CompensationRecommendationsDTO,
)
from shared.dto.api import AppResponse


class CompensationContourResponseDTO(AppResponse[CompensationContourDTO]): ...


class CompensationRecommendationsResponseDTO(
    AppResponse[CompensationRecommendationsDTO],
): ...


class CompensationDonorsResponseDTO(AppResponse[CompensationDonorsDTO]): ...


class CompensationLossesResponseDTO(AppResponse[CompensationLossesDTO]): ...
