from apps.telemetry.dto.internal.telemetry import TelemetryDTO
from shared.dto.api import AppResponse


class ListTelemetryResponseDTO(AppResponse[list[TelemetryDTO]]): ...
