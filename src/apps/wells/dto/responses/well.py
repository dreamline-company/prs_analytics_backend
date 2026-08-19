from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.internal.well_coords import WellCoordMapPointDTO
from apps.wells.dto.internal.well_matrix import WellMatrixItemDTO
from apps.wells.dto.internal.well_matrix_incidents import WellMatrixIncidentDTO
from shared.dto.api import AppResponse


class SearchWellsResponseDTO(AppResponse[list[WellShortDTO]]): ...


class WellsMatrixResponseDTO(AppResponse[list[WellMatrixItemDTO]]): ...


class WellCardResponseDTO(AppResponse[WellCardDTO]): ...


class WellMatrixIncidentsResponseDTO(AppResponse[list[WellMatrixIncidentDTO]]): ...


class WellCoordsResponseDTO(AppResponse[list[WellCoordMapPointDTO]]): ...
