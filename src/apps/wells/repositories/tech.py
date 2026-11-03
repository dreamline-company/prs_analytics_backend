from collections.abc import Sequence

from apps.wells.dto.internal.repositories.tech import (
    TechDTO,
    TechHistoryDTO,
    TechTypeDTO,
    WellTechDTO,
)
from apps.wells.models.tech import Tech, TechHistory, TechType, WellTech
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class _MirrorRepository[
    DTOT: (TechTypeDTO, TechDTO, TechHistoryDTO, WellTechDTO),
    ModelT: (TechType, Tech, TechHistory, WellTech),
](AsyncAlchemyRepository[DTOT, DTOT, ModelT]):
    """Зеркало одной таблицы ABAI: читается целиком и сверяется по ``abai_id``."""

    # Размер IN-списка при удалении исчезнувших в источнике строк.
    DELETE_CHUNK_SIZE = 1000

    async def list_all(self) -> Sequence[ModelT]:
        return await self.get_list(QuerySpec(order_by=(self.model.abai_id,)))

    async def update_by_abai_id(self, data: DTOT) -> None:
        await self.update(
            data,
            filters=(self.model.abai_id == data.abai_id,),
            partial=False,
        )

    async def delete_by_abai_ids(self, abai_ids: Sequence[int]) -> None:
        for start in range(0, len(abai_ids), self.DELETE_CHUNK_SIZE):
            chunk = abai_ids[start : start + self.DELETE_CHUNK_SIZE]
            await self.delete(filters=(self.model.abai_id.in_(chunk),))


class TechTypeRepository(_MirrorRepository[TechTypeDTO, TechType]):
    model = TechType


class TechRepository(_MirrorRepository[TechDTO, Tech]):
    model = Tech


class TechHistoryRepository(_MirrorRepository[TechHistoryDTO, TechHistory]):
    model = TechHistory


class WellTechRepository(_MirrorRepository[WellTechDTO, WellTech]):
    model = WellTech
