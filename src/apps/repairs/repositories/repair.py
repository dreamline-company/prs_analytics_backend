from collections.abc import Sequence
from datetime import date, datetime, time

from sqlalchemy import func, or_, select

from apps.repairs.dto.internal.repair import CurrentRepairDTO
from apps.repairs.dto.internal.repositories.repair import (
    CreateRepairDTO,
    CreateRepairTypeDTO,
    UpdateRepairDTO,
    UpdateRepairTypeDTO,
)
from apps.repairs.models.repair import Repair, RepairType
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class RepairTypeRepository(
    AsyncAlchemyRepository[CreateRepairTypeDTO, UpdateRepairTypeDTO, RepairType],
):
    model = RepairType

    async def get_by_id(self, repair_type_id: int) -> RepairType | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairType.id == repair_type_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> RepairType | None:
        return await self.get_one(
            QuerySpec(
                filters=(RepairType.abai_id == abai_id,),
            ),
        )

    async def update_by_abai_id(
        self,
        abai_id: int,
        data: UpdateRepairTypeDTO,
    ) -> RepairType:
        return await self.update(
            data=data,
            filters=(RepairType.abai_id == abai_id,),
        )

    async def delete_by_id(self, repair_type_id: int) -> None:
        await self.delete(filters=(RepairType.id == repair_type_id,))

    async def list_by_ids(self, ids: Sequence[int]) -> Sequence[RepairType]:
        if not ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(RepairType.id.in_(ids),), order_by=(RepairType.id,)),
        )


class RepairRepository(
    AsyncAlchemyRepository[CreateRepairDTO, UpdateRepairDTO, Repair],
):
    model = Repair

    async def get_by_id(self, repair_id: int) -> Repair | None:
        return await self.get_one(
            QuerySpec(
                filters=(Repair.id == repair_id,),
            ),
        )

    async def get_by_abai_id(self, abai_id: int) -> Repair | None:
        return await self.get_one(
            QuerySpec(
                filters=(Repair.abai_id == abai_id,),
            ),
        )

    async def list_by_well_abai_id(self, well_id: int) -> Sequence[Repair]:
        return await self.get_list(
            QuerySpec(
                filters=(Repair.abai_well_id == well_id,),
                order_by=(Repair.start_time.desc(),),
            ),
        )

    async def list_current_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
        *,
        now: datetime,
    ) -> dict[int, CurrentRepairDTO]:
        """Идущий ремонт каждой скважины: начался и не закрыт.

        Связь идёт по ``abai_well_id``: ``Repair.well_id`` загрузчиком не
        заполняется. Если незакрытых ремонтов у скважины несколько, берётся
        начатый позже — старые почти всегда означают незакрытую запись, а не
        второй параллельный ремонт; DISTINCT ON оставляет от каждой скважины
        ровно одну строку. Название типа тянется тем же запросом, чтобы на
        матрицу НГДУ не приходилось по запросу на скважину.
        """
        if not abai_well_ids:
            return {}

        stmt = (
            select(Repair, RepairType.name_ru)
            .join(RepairType, RepairType.abai_id == Repair.repair_type_id, isouter=True)
            .where(
                Repair.abai_well_id.in_(abai_well_ids),
                Repair.is_open,
                Repair.start_time <= now,
            )
            .distinct(Repair.abai_well_id)
            .order_by(
                Repair.abai_well_id,
                Repair.start_time.desc(),
                Repair.abai_id.desc(),
            )
        )
        result = await self.session.execute(stmt)

        current: dict[int, CurrentRepairDTO] = {}
        for repair, type_name in result.all():
            dto = CurrentRepairDTO.model_validate(repair)
            dto.repair_type_name_ru = type_name
            current[repair.abai_well_id] = dto
        return current

    async def list_by_ids(self, repair_ids: Sequence[int]) -> Sequence[Repair]:
        if not repair_ids:
            return ()
        return await self.get_list(
            QuerySpec(filters=(Repair.id.in_(repair_ids),)),
        )

    async def find_covering_date(
        self,
        well_id: int,
        target_date: date,
    ) -> Repair | None:
        day_start = datetime.combine(target_date, time.min)
        day_end = datetime.combine(target_date, time.max)
        rs = await self.get_list(
            QuerySpec(
                filters=(
                    Repair.well_id == well_id,
                    Repair.abai_deleted_at.is_(None),
                    Repair.start_time <= day_end,
                    or_(Repair.end_time.is_(None), Repair.end_time >= day_start),
                ),
                order_by=(Repair.start_time.desc(),),
                limit=1,
            ),
        )
        return rs[0] if rs else None

    async def list_covering_range(
        self,
        well_ids: Sequence[int],
        abai_well_ids: Sequence[int],
        *,
        date_from: date,
        date_to: date,
    ) -> Sequence[Repair]:
        """Ремонты этих скважин, пересекающие интервал [date_from, date_to].

        Батчевый аналог ``find_covering_date`` — один запрос вместо запроса на
        каждую сводку. Скважина ищется по обоим ключам: ``Repair.well_id``
        загрузчиком не заполняется, реальная связь живёт в ``abai_well_id``.
        """
        if not well_ids and not abai_well_ids:
            return ()

        range_start = datetime.combine(date_from, time.min)
        range_end = datetime.combine(date_to, time.max)
        return await self.get_list(
            QuerySpec(
                filters=(
                    or_(
                        Repair.well_id.in_(well_ids),
                        Repair.abai_well_id.in_(abai_well_ids),
                    ),
                    Repair.abai_deleted_at.is_(None),
                    Repair.start_time <= range_end,
                    or_(Repair.end_time.is_(None), Repair.end_time >= range_start),
                ),
                order_by=(Repair.start_time.desc(),),
            ),
        )

    async def count_by_well_abai_ids_since(
        self,
        abai_well_ids: Sequence[int],
        *,
        since: datetime,
    ) -> dict[int, int]:
        if not abai_well_ids:
            return {}
        stmt = (
            select(Repair.abai_well_id, func.count(Repair.id))
            .where(
                Repair.abai_well_id.in_(abai_well_ids),
                Repair.start_time >= since,
            )
            .group_by(Repair.abai_well_id)
        )
        result = await self.session.execute(stmt)
        return {row[0]: row[1] for row in result.all()}

    async def list_active_by_well_abai_ids(
        self,
        abai_well_ids: Sequence[int],
    ) -> Sequence[Repair]:
        if not abai_well_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(
                    Repair.abai_well_id.in_(abai_well_ids),
                    Repair.is_open,
                ),
                order_by=(Repair.start_time.desc(),),
            ),
        )

    async def list_by_repair_type_id(self, repair_type_id: int) -> Sequence[Repair]:
        return await self.get_list(
            QuerySpec(
                filters=(Repair.repair_type_id == repair_type_id,),
                order_by=(Repair.start_time,),
            ),
        )

    async def update_by_id(self, repair_id: int, data: UpdateRepairDTO) -> Repair:
        return await self.update(
            data=data,
            filters=(Repair.id == repair_id,),
        )

    async def update_by_abai_id(self, abai_id: int, data: UpdateRepairDTO) -> Repair:
        return await self.update(
            data=data,
            filters=(Repair.abai_id == abai_id,),
        )

    async def delete_by_id(self, repair_id: int) -> None:
        await self.delete(filters=(Repair.id == repair_id,))

    async def get_last_by_abai_id(self) -> Repair | None:
        rs = await self.get_list(
            spec=QuerySpec(order_by=(Repair.abai_id.desc(),), limit=1),
        )
        return rs[0] if rs else None
