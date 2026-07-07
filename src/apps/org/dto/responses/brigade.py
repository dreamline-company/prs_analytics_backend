from apps.org.dto.internal.brigade import BrigadeDTO, BrigadesKPIDTO
from shared.dto.api import AppResponse


class ListBrigadesResponseDTO(AppResponse[list[BrigadeDTO]]): ...


class BrigadesKPIResponseDTO(AppResponse[BrigadesKPIDTO]): ...
