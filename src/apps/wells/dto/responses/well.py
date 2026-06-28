from apps.wells.dto.internal.well import WellShortDTO
from shared.dto.api import AppResponse


class SearchWellsResponseDTO(AppResponse[list[WellShortDTO]]): ...
