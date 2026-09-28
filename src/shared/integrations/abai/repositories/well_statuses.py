from collections.abc import Sequence

from shared.integrations.abai.models import Reason, WellStatus, WellStatusType
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class ABAIWellStatusTypeRepository(
    ABAIReadOnlyRepository[WellStatusType],
):
    model = WellStatusType

    async def list_all(self) -> Sequence[WellStatusType]:
        return await self.get_list(QuerySpec(order_by=(WellStatusType.id.asc(),)))


class ABAIReasonRepository(
    ABAIReadOnlyRepository[Reason],
):
    model = Reason

    async def list_all(self) -> Sequence[Reason]:
        return await self.get_list(QuerySpec(order_by=(Reason.id.asc(),)))


class ABAIWellStatusRepository(
    ABAIReadOnlyRepository[WellStatus],
):
    model = WellStatus

    async def list_after_id(self, last_id: int, *, limit: int) -> Sequence[WellStatus]:
        """Батч интервалов статусов с id > last_id (keyset-пагинация)."""
        return await self.get_list(
            QuerySpec(
                filters=(WellStatus.id > last_id,),
                order_by=(WellStatus.id.asc(),),
                limit=limit,
            ),
        )

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[WellStatus]:
        if not ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(WellStatus.id.in_(ids),),
                order_by=(WellStatus.id.asc(),),
            ),
        )
