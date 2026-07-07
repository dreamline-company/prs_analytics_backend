from apps.org.dto.internal.brigade import BrigadeDTO
from apps.org.dto.queries.brigade import ListBrigadesByNGDUIdQuery
from apps.org.repositories import UniqueBrigadeRepository


class ListBrigadesByNGDUIdUseCase:
    def __init__(self, unique_brigade_repository: UniqueBrigadeRepository) -> None:
        self.unique_brigade_repository = unique_brigade_repository

    async def execute(
        self,
        query: ListBrigadesByNGDUIdQuery,
    ) -> list[BrigadeDTO]:
        brigades = await self.unique_brigade_repository.list_by_ngdu_id(query.ngdu_id)
        return [BrigadeDTO.model_validate(brigade) for brigade in brigades]
