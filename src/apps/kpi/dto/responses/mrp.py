from apps.kpi.dto.internal.mrp import MrpDynamicsDTO, MrpSummaryDTO
from shared.dto.api import AppResponse


class MrpSummaryResponseDTO(AppResponse[MrpSummaryDTO]): ...


class MrpDynamicsResponseDTO(AppResponse[MrpDynamicsDTO]): ...
