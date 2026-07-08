from apps.org.dto.internal.brigade import (
    BrigadeDangerZoneItemDTO,
    BrigadeDTO,
    BrigadeRepairStateDTO,
    BrigadesKPIDTO,
)
from shared.dto.api import AppResponse


class ListBrigadesResponseDTO(AppResponse[list[BrigadeDTO]]): ...


class BrigadesKPIResponseDTO(AppResponse[BrigadesKPIDTO]): ...


class BrigadeRepairStateResponseDTO(AppResponse[BrigadeRepairStateDTO]): ...


class BrigadesInDangerZoneResponseDTO(AppResponse[list[BrigadeDangerZoneItemDTO]]): ...
