from collections.abc import Iterable, Sequence
from datetime import date

from sqlalchemy import select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.repairs.dto.internal.repositories.reports import (
    CreateRepairSummaryDTO,
    UpdateRepairSummaryDTO,
)
from apps.repairs.models.reports import RepairSummary
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec

# Ключ уникальности сводки: скважина × сутки × смена.
SummaryKey = tuple[int, date, int]

# PostgreSQL не принимает больше 32767 параметров на запрос, а сводки грузятся
# месячными файлами по несколько тысяч строк — режем на чанки.
_UPSERT_CHUNK = 2000
_KEYS_CHUNK = 5000


class RepairSummaryRepository(
    AsyncAlchemyRepository[
        CreateRepairSummaryDTO,
        UpdateRepairSummaryDTO,
        RepairSummary,
    ],
):
    model = RepairSummary

    async def list_by_well_id(self, well_id: int) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.well_id == well_id,),
                order_by=(RepairSummary.date,),
            ),
        )

    async def list_by_repair_id(self, repair_id: int) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.repair_id == repair_id,),
                order_by=(RepairSummary.date,),
            ),
        )

    async def list_existing_keys(
        self,
        keys: Iterable[SummaryKey],
    ) -> set[SummaryKey]:
        """Вернуть те ключи (well_id, date, shift), что уже есть в БД."""
        keys_list = list(keys)
        if not keys_list:
            return set()

        found: set[SummaryKey] = set()
        for offset in range(0, len(keys_list), _KEYS_CHUNK):
            chunk = keys_list[offset : offset + _KEYS_CHUNK]
            qs = select(
                RepairSummary.well_id,
                RepairSummary.date,
                RepairSummary.shift_type_number,
            ).where(
                tuple_(
                    RepairSummary.well_id,
                    RepairSummary.date,
                    RepairSummary.shift_type_number,
                ).in_(chunk),
            )
            rows = await self.fetch_all(qs)
            found.update(
                (row["well_id"], row["date"], row["shift_type_number"]) for row in rows
            )
        return found

    async def bulk_upsert(self, data: Sequence[CreateRepairSummaryDTO]) -> None:
        """Вставить сводки, обновив содержимое при совпадении ключа.

        Повторная загрузка того же файла не падает на UNIQUE, а перезаписывает
        отчёт свежей версией — сводки за сутки уточняются задним числом.
        """
        if not data:
            return

        values = [item.model_dump() for item in data]
        for offset in range(0, len(values), _UPSERT_CHUNK):
            stmt = pg_insert(RepairSummary).values(
                values[offset : offset + _UPSERT_CHUNK],
            )
            await self.session.execute(
                stmt.on_conflict_do_update(
                    index_elements=[
                        RepairSummary.well_id,
                        RepairSummary.date,
                        RepairSummary.shift_type_number,
                    ],
                    set_={
                        "second_well_id": stmt.excluded.second_well_id,
                        "brigade_number": stmt.excluded.brigade_number,
                        "pump_type": stmt.excluded.pump_type,
                        "car": stmt.excluded.car,
                        "device_number": stmt.excluded.device_number,
                        "shift_details": stmt.excluded.shift_details,
                    },
                ),
            )

    async def list_by_date(self, summary_date: date) -> Sequence[RepairSummary]:
        return await self.get_list(
            QuerySpec(
                filters=(RepairSummary.date == summary_date,),
                order_by=(RepairSummary.id,),
            ),
        )

    async def update_by_id(
        self,
        repair_summary_id: int,
        data: UpdateRepairSummaryDTO,
    ) -> RepairSummary:
        return await self.update(
            data=data,
            filters=(RepairSummary.id == repair_summary_id,),
        )

    async def delete_by_id(self, repair_summary_id: int) -> None:
        await self.delete(filters=(RepairSummary.id == repair_summary_id,))
