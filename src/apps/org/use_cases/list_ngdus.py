from apps.org.dto.internal.ngdu import NGDUShortDTO
from shared.integrations.cm.repositories.ngdu import CMNGDURepository


class ListNGDUsUseCase:
    def __init__(self, cm_ngdu_repository: CMNGDURepository) -> None:
        self.cm_ngdu_repository = cm_ngdu_repository

    async def execute(self) -> list[NGDUShortDTO]:
        ngdus = await self.cm_ngdu_repository.list_all()
        return [NGDUShortDTO.model_validate(ngdu) for ngdu in ngdus]
