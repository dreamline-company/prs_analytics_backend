from collections.abc import Sequence

from shared.integrations.abai.models import RepairWorkType
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIRepairWorkTypeRepository(
    ABAIReadOnlyRepository[RepairWorkType],
):
    model = RepairWorkType

    async def get_by_id(self, repair_work_type_id: int) -> RepairWorkType | None:
        return await super().get_by_id(repair_work_type_id)

    async def list_by_ids(
        self,
        repair_work_type_ids: Sequence[int],
    ) -> Sequence[RepairWorkType]:
        if not repair_work_type_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(RepairWorkType.id.in_(repair_work_type_ids),),
                order_by=(RepairWorkType.id,),
            ),
        )
