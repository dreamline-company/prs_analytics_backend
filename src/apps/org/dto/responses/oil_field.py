from apps.org.dto.internal.oil_field import OilFieldDTO
from shared.dto.api import AppResponse


class ListOilFieldsResponseDTO(AppResponse[list[OilFieldDTO]]): ...
