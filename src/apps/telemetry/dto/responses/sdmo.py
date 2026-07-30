from apps.telemetry.dto.internal.sdmo import SdmoParametersDTO
from shared.dto.api import AppResponse


class ListSdmoParametersResponseDTO(AppResponse[list[SdmoParametersDTO]]): ...
