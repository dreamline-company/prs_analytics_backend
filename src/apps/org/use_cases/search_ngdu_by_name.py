from apps.org.dto.internal.ngdu import NGDUShortDTO
from apps.org.dto.queries.ngdu import SearchNGDUByNameQuery
from shared.integrations.cm.repositories.ngdu import CMNGDURepository


class SearchNGDUByNameUseCase:
    def __init__(self, cm_ngdu_repository: CMNGDURepository) -> None:
        self.cm_ngdu_repository = cm_ngdu_repository

    async def execute(
        self,
        query: SearchNGDUByNameQuery,
    ) -> list[NGDUShortDTO]:
        ngdus = await self.cm_ngdu_repository.search_by_name(
            query.name,
            limit=query.limit,
        )
        return [NGDUShortDTO.model_validate(ngdu) for ngdu in ngdus]
