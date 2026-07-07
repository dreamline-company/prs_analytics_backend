from apps.wells.dto.internal.well import WellMatrixItemDTO, WellShortDTO
from shared.dto.api import AppResponse


class SearchWellsResponseDTO(AppResponse[list[WellShortDTO]]): ...


class WellsMatrixResponseDTO(AppResponse[list[WellMatrixItemDTO]]): ...
