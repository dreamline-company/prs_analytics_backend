from collections.abc import Sequence

from shared.integrations.abai.models import WellWorkover
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellWorkoverRepository(
    ABAIReadOnlyRepository[WellWorkover],
):
    model = WellWorkover

    async def list_by_ids(
        self,
        well_workover_ids: Sequence[int],
    ) -> Sequence[WellWorkover]:
        if not well_workover_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellWorkover.id.in_(well_workover_ids),),
                order_by=(WellWorkover.id.asc(),),
            ),
        )

    async def list_by_well(self, well_id: int) -> Sequence[WellWorkover]:
        return await self.get_list(
            QuerySpec(
                filters=(WellWorkover.well == well_id,),
                order_by=(WellWorkover.dbeg.asc(),),
            ),
        )

    async def list_by_repair_work_type(
        self,
        repair_work_type_id: int,
    ) -> Sequence[WellWorkover]:
        return await self.get_list(
            QuerySpec(
                filters=(WellWorkover.repair_work_type == repair_work_type_id,),
                order_by=(WellWorkover.dbeg.asc(),),
            ),
        )
