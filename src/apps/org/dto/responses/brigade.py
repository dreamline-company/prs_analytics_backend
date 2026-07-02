from apps.org.dto.internal.brigade import BrigadeDTO
from shared.dto.api import AppResponse


class ListBrigadesResponseDTO(AppResponse[list[BrigadeDTO]]): ...
