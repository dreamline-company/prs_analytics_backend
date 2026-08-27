from apps.detectors.dto.internal.incident import DetectorIncidentDTO
from shared.dto.api import AppResponse


class ListDetectorIncidentsResponseDTO(AppResponse[list[DetectorIncidentDTO]]): ...
