from collections.abc import Sequence

from shared.integrations.abai.models import Brigade
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIBrigadeRepository(
    ABAIReadOnlyRepository[Brigade],
):
    model = Brigade

    async def list_by_ids(self, brigade_ids: Sequence[int]) -> Sequence[Brigade]:
        if not brigade_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Brigade.id.in_(brigade_ids),),
                order_by=(Brigade.id,),
            ),
        )

    async def list_by_org(self, org_id: int) -> Sequence[Brigade]:
        return await self.get_list(
            QuerySpec(
                filters=(Brigade.org == org_id,),
                order_by=(Brigade.id,),
            ),
        )
