from apps.repairs.dto.internal.repair import RepairDTO
from shared.dto.api import AppResponse


class ListRepairsResponseDTO(AppResponse[list[RepairDTO]]): ...
