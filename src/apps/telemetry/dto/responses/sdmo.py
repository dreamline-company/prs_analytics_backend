from apps.telemetry.dto.internal.sdmo import SdmoDriveStationDTO, SdmoParametersDTO
from shared.dto.api import AppResponse


class ListSdmoParametersResponseDTO(AppResponse[list[SdmoParametersDTO]]): ...


class ListSdmoDriveSegmentsResponseDTO(AppResponse[list[SdmoDriveStationDTO]]): ...
