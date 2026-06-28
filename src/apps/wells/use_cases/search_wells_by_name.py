from apps.wells.dto.internal.well import WellShortDTO
from apps.wells.dto.queries.well import SearchWellsByNameQuery
from apps.wells.repositories.well import WellRepository


class SearchWellsByNameUseCase:
    def __init__(self, well_repository: WellRepository) -> None:
        self.well_repository = well_repository

    async def execute(
        self,
        query: SearchWellsByNameQuery,
    ) -> list[WellShortDTO]:
        wells = await self.well_repository.search_by_name(
            query.name,
            limit=query.limit,
        )
        return [WellShortDTO.model_validate(well) for well in wells]
