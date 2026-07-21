from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.internal.well_matrix import WellMatrixItemDTO
from shared.dto.api import AppResponse


class SearchWellsResponseDTO(AppResponse[list[WellShortDTO]]): ...


class WellsMatrixResponseDTO(AppResponse[list[WellMatrixItemDTO]]): ...
