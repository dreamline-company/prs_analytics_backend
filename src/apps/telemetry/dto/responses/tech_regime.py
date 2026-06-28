from apps.telemetry.dto.internal.tech_regime import TechRegimeDTO
from shared.dto.api import AppResponse


class ListTechRegimeResponseDTO(AppResponse[list[TechRegimeDTO]]): ...
