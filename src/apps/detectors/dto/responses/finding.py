from apps.detectors.dto.internal.finding import DetectorFindingDTO
from shared.dto.api import AppResponse


class ListDetectorFindingsResponseDTO(AppResponse[list[DetectorFindingDTO]]): ...
