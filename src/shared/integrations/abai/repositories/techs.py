from collections.abc import Sequence

from shared.integrations.abai.models import Tech, TechHistory, TechType, WellTech
from shared.integrations.abai.repositories.base import ABAIReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class _ABAIKeysetRepository[ModelT: (TechType, Tech, TechHistory, WellTech)](
    ABAIReadOnlyRepository[ModelT],
):
    """Маленький справочник ABAI, который читается целиком батчами по id."""

    BATCH_SIZE = 5000

    async def list_all(self) -> list[ModelT]:
        rows: list[ModelT] = []
        last_id = 0
        while True:
            batch = await self.list_after_id(last_id, limit=self.BATCH_SIZE)
            rows.extend(batch)
            if len(batch) < self.BATCH_SIZE:
                return rows
            last_id = batch[-1].id

    async def list_after_id(self, last_id: int, *, limit: int) -> Sequence[ModelT]:
        """Батч строк с id > last_id (keyset-пагинация)."""
        return await self.get_list(
            QuerySpec(
                filters=(self.model.id > last_id,),
                order_by=(self.model.id.asc(),),
                limit=limit,
            ),
        )


class ABAITechTypeRepository(_ABAIKeysetRepository[TechType]):
    model = TechType


class ABAITechRepository(_ABAIKeysetRepository[Tech]):
    model = Tech


class ABAITechHistoryRepository(_ABAIKeysetRepository[TechHistory]):
    model = TechHistory


class ABAIWellTechRepository(_ABAIKeysetRepository[WellTech]):
    model = WellTech
