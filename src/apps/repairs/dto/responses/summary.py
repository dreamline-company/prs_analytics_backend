from apps.repairs.dto.internal.summary import RepairSummaryDTO
from shared.dto.api import AppResponse


class ListRepairSummariesResponseDTO(AppResponse[list[RepairSummaryDTO]]): ...
