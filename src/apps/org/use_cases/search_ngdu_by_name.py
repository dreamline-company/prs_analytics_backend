from apps.org.dto.internal.ngdu import NGDUShortDTO
from apps.org.dto.queries.ngdu import SearchNGDUByNameQuery
from apps.org.repositories.org import OrgRepository
from shared.constants.ngdu import AbaiNGDUIDsEnum


class SearchNGDUByNameUseCase:
    def __init__(self, org_repository: OrgRepository) -> None:
        self.org_repository = org_repository

    async def execute(
        self,
        query: SearchNGDUByNameQuery,
    ) -> list[NGDUShortDTO]:
        orgs = await self.org_repository.search_by_name_in_abai_ids(
            query.name,
            [item.value for item in AbaiNGDUIDsEnum],
            limit=query.limit,
        )
        return [NGDUShortDTO(id=org.id, name=org.name_ru) for org in orgs]
