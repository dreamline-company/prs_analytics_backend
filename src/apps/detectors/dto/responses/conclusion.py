from apps.detectors.dto.internal.conclusion import (
    ConclusionFeedbackDTO,
    DetectorConclusionDTO,
    WellAiConclusionDTO,
)
from shared.dto.api import AppResponse


class WellAiConclusionResponseDTO(AppResponse[WellAiConclusionDTO]): ...


class ConclusionHistoryResponseDTO(AppResponse[list[DetectorConclusionDTO]]): ...


class ConclusionFeedbackResponseDTO(AppResponse[ConclusionFeedbackDTO]): ...
