from apps.org.dto.internal.brigade import BrigadeDTO
from apps.org.dto.queries.brigade import ListBrigadesByNGDUIdQuery
from shared.integrations.cm.repositories.brigades import CMBrigadeRepository


class ListBrigadesByNGDUIdUseCase:
    def __init__(self, cm_brigade_repository: CMBrigadeRepository) -> None:
        self.cm_brigade_repository = cm_brigade_repository

    async def execute(
        self,
        query: ListBrigadesByNGDUIdQuery,
    ) -> list[BrigadeDTO]:
        brigades = await self.cm_brigade_repository.list_by_ngdu_id(query.ngdu_id)
        # violations_count and is_in_repair are placeholders (0 / False) for now.
        return [BrigadeDTO.model_validate(brigade) for brigade in brigades]
