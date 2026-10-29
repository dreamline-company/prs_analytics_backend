from apps.wells.dto.internal.dynamogram import WellDynamogramDTO
from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.internal.well_coords import WellCoordMapPointDTO
from apps.wells.dto.internal.well_matrix import WellMatrixItemDTO
from apps.wells.dto.internal.well_matrix_incidents import WellMatrixIncidentDTO
from apps.wells.dto.internal.well_overview import WellOverviewDTO
from apps.wells.dto.internal.well_production_profile import WellProductionProfileDTO
from apps.wells.dto.internal.well_timeline import WellTimelineDTO
from shared.dto.api import AppResponse


class SearchWellsResponseDTO(AppResponse[list[WellShortDTO]]): ...


class WellsMatrixResponseDTO(AppResponse[list[WellMatrixItemDTO]]): ...


class WellCardResponseDTO(AppResponse[WellCardDTO]): ...


class WellMatrixIncidentsResponseDTO(AppResponse[list[WellMatrixIncidentDTO]]): ...


class WellCoordsResponseDTO(AppResponse[list[WellCoordMapPointDTO]]): ...


class WellDynamogramsResponseDTO(AppResponse[list[WellDynamogramDTO]]): ...


class WellTimelineResponseDTO(AppResponse[WellTimelineDTO]): ...


class WellOverviewResponseDTO(AppResponse[WellOverviewDTO]): ...


class WellProductionProfileResponseDTO(AppResponse[WellProductionProfileDTO]): ...
