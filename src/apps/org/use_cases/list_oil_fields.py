from apps.org.dto.internal.oil_field import OilFieldDTO
from apps.org.dto.queries.oil_field import ListOilFieldsQuery
from apps.org.repositories.oil_field import OilFieldRepository


class ListOilFieldsUseCase:
    def __init__(self, oil_field_repository: OilFieldRepository) -> None:
        self.oil_field_repository = oil_field_repository

    async def execute(self, query: ListOilFieldsQuery) -> list[OilFieldDTO]:
        oil_fields = await self.oil_field_repository.list_oil_fields(
            ngdu_id=query.ngdu_id,
        )
        return [OilFieldDTO.model_validate(item) for item in oil_fields]
