from collections.abc import Collection, Sequence
from datetime import datetime

from sqlalchemy import func, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.gps.dto.internal.repositories.gps import (
    CreateGpsMessageDTO,
    CreateGpsUnitDTO,
    UpdateGpsMessageDTO,
    UpdateGpsUnitDTO,
)
from apps.gps.models.message import GpsMessage
from apps.gps.models.unit import GpsUnit
from apps.wells.repositories.gdis import rows_per_statement
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class GpsUnitRepository(
    AsyncAlchemyRepository[CreateGpsUnitDTO, UpdateGpsUnitDTO, GpsUnit],
):
    model = GpsUnit

    async def upsert_many(self, data: Sequence[CreateGpsUnitDTO]) -> None:
        """Upsert справочника по ``argus_id``.

        Обновляются поля из Аргуса; ``tracked`` и курсор трека — нет, их ведёт
        загрузчик треков. Машина, снова пришедшая в ответе, перестаёт быть
        удалённой.
        """
        if not data:
            return
        by_key = {item.argus_id: item for item in data}
        rows = [item.model_dump() for item in by_key.values()]
        step = rows_per_statement(len(rows[0]))
        for start in range(0, len(rows), step):
            stmt = pg_insert(GpsUnit).values(rows[start : start + step])
            set_ = {
                column: stmt.excluded[column]
                for column in rows[0]
                if column != "argus_id"
            }
            set_["removed_at"] = None
            set_["updated_at"] = func.now()
            stmt = stmt.on_conflict_do_update(index_elements=["argus_id"], set_=set_)
            await self.session.execute(stmt)

    async def mark_removed(self, seen_argus_ids: Collection[int], at: datetime) -> int:
        """Пометить удалёнными машины, которых нет среди ``seen_argus_ids``."""
        result = await self.session.execute(
            update(GpsUnit)
            .where(
                GpsUnit.argus_id.not_in(list(seen_argus_ids)),
                GpsUnit.removed_at.is_(None),
            )
            .values(removed_at=at, tracked=False, updated_at=func.now()),
        )
        return result.rowcount or 0

    async def list_active(self) -> Sequence[GpsUnit]:
        """Машины, которые сейчас есть в Аргусе."""
        return await self.get_list(
            QuerySpec(
                filters=(GpsUnit.removed_at.is_(None),),
                order_by=(GpsUnit.id,),
            ),
        )

    async def list_tracked(self) -> Sequence[GpsUnit]:
        return await self.get_list(
            QuerySpec(
                filters=(GpsUnit.tracked.is_(True), GpsUnit.removed_at.is_(None)),
                order_by=(GpsUnit.id,),
            ),
        )

    async def list_by_argus_ids(self, argus_ids: Collection[int]) -> Sequence[GpsUnit]:
        if not argus_ids:
            return []
        return await self.get_list(
            QuerySpec(
                filters=(GpsUnit.argus_id.in_(list(argus_ids)),),
                order_by=(GpsUnit.id,),
            ),
        )

    async def set_tracked(self, unit_ids: Collection[int]) -> tuple[int, int]:
        """``tracked`` = ровно у ``unit_ids``. Возвращает (включено, выключено)."""
        ids = list(unit_ids)
        enabled = await self.session.execute(
            update(GpsUnit)
            .where(GpsUnit.id.in_(ids), GpsUnit.tracked.is_(False))
            .values(tracked=True, updated_at=func.now()),
        )
        disabled = await self.session.execute(
            update(GpsUnit)
            .where(GpsUnit.id.not_in(ids), GpsUnit.tracked.is_(True))
            .values(tracked=False, updated_at=func.now()),
        )
        return enabled.rowcount or 0, disabled.rowcount or 0

    async def advance_cursor(self, unit_id: int, at: datetime) -> None:
        """Сдвинуть курсор трека вперёд до ``at``; назад он не уходит
        (перечитка начинается раньше курсора)."""
        await self.session.execute(
            update(GpsUnit)
            .where(GpsUnit.id == unit_id)
            .values(
                track_cursor_at=func.greatest(
                    func.coalesce(GpsUnit.track_cursor_at, at),
                    at,
                ),
            ),
        )


class GpsMessageRepository(
    AsyncAlchemyRepository[CreateGpsMessageDTO, UpdateGpsMessageDTO, GpsMessage],
):
    model = GpsMessage

    async def insert_ignore(self, data: Sequence[CreateGpsMessageDTO]) -> int:
        """Вставить точки, пропуская уже загруженные. Возвращает число новых."""
        if not data:
            return 0
        rows = [item.model_dump() for item in data]
        inserted = 0
        step = rows_per_statement(len(rows[0]))
        for start in range(0, len(rows), step):
            stmt = (
                pg_insert(GpsMessage)
                .values(rows[start : start + step])
                .on_conflict_do_nothing(index_elements=["unit_id", "time"])
                .returning(GpsMessage.unit_id)
            )
            result = await self.session.execute(stmt)
            inserted += len(result.all())
        return inserted
