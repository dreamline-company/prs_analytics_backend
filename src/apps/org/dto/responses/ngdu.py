from apps.org.dto.internal.ngdu import NGDUShortDTO
from shared.dto.api import AppResponse


class SearchNGDUResponseDTO(AppResponse[list[NGDUShortDTO]]): ...
