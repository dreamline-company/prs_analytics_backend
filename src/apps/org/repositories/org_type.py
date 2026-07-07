from apps.org.dto.internal.repositories.org_type import (
    CreateOrgTypeDTO,
    UpdateOrgTypeDTO,
)
from apps.org.models.org import OrgType
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class OrgTypeRepository(
    AsyncAlchemyRepository[CreateOrgTypeDTO, UpdateOrgTypeDTO, OrgType],
):
    model = OrgType

    async def get_by_id(self, org_type_id: int) -> OrgType | None:
        return await self.get_one(
            QuerySpec(
                filters=(OrgType.id == org_type_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> OrgType | None:
        return await self.get_one(
            QuerySpec(
                filters=(OrgType.abai_id == abai_id,),
            ),
        )

    async def delete_by_id(self, org_type_id: int) -> None:
        await self.delete(filters=(OrgType.id == org_type_id,))
