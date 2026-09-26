from collections.abc import Sequence
from datetime import datetime

from core.settings import get_settings
from shared.integrations.cm.models import BrigadeErrorScreen
from shared.integrations.cm.repositories.base import CMReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


def to_local_naive(value: datetime) -> datetime:
    """Момент из CM (``timestamptz``, приходит в UTC) -> наивное местное время.

    Всё приложение живёт в наивном местном времени (``Repair.start_time``,
    ``datetime.now()`` в use case), поэтому экраны нарушений приводятся к нему
    на границе репозитория — иначе сравнение с датами ремонта падает с
    «can't compare offset-naive and offset-aware datetimes».
    """
    if value.tzinfo is None:
        return value
    return value.astimezone(get_settings().ZONE_INFO).replace(tzinfo=None)


def to_aware(value: datetime) -> datetime:
    """Наивное местное время -> aware для фильтра по ``timestamptz``."""
    if value.tzinfo is not None:
        return value
    return value.replace(tzinfo=get_settings().ZONE_INFO)


class CMBrigadeErrorScreenRepository(
    CMReadOnlyRepository[BrigadeErrorScreen],
):
    model = BrigadeErrorScreen

    async def get_one(
        self,
        spec: QuerySpec | None = None,
    ) -> BrigadeErrorScreen | None:
        row = await super().get_one(spec)
        if row is not None:
            row.timestamp = to_local_naive(row.timestamp)
        return row

    async def get_list(
        self,
        spec: QuerySpec | None = None,
    ) -> Sequence[BrigadeErrorScreen]:
        rows = await super().get_list(spec)
        for row in rows:
            row.timestamp = to_local_naive(row.timestamp)
        return rows

    async def get_by_id(
        self,
        brigade_error_screen_id: int,
    ) -> BrigadeErrorScreen | None:
        return await self.get_one(
            QuerySpec(
                filters=(BrigadeErrorScreen.id == brigade_error_screen_id,),
            ),
        )

    async def list_by_ids(
        self,
        brigade_error_screen_ids: Sequence[int],
    ) -> Sequence[BrigadeErrorScreen]:
        if not brigade_error_screen_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(BrigadeErrorScreen.id.in_(brigade_error_screen_ids),),
                order_by=(BrigadeErrorScreen.id,),
            ),
        )

    async def list_by_brigade_id(
        self,
        brigade_id: int,
    ) -> Sequence[BrigadeErrorScreen]:
        return await self.get_list(
            QuerySpec(
                filters=(BrigadeErrorScreen.brigade_id == brigade_id,),
                order_by=(BrigadeErrorScreen.timestamp.desc(),),
            ),
        )

    async def list_by_brigade_ids_in_range(
        self,
        brigade_ids: Sequence[int],
        *,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[BrigadeErrorScreen]:
        if not brigade_ids:
            return ()
        return await self.get_list(
            QuerySpec(
                filters=(
                    BrigadeErrorScreen.brigade_id.in_(brigade_ids),
                    BrigadeErrorScreen.timestamp >= to_aware(start_time),
                    BrigadeErrorScreen.timestamp <= to_aware(end_time),
                ),
                order_by=(BrigadeErrorScreen.timestamp,),
            ),
        )

    async def list_by_processed_status(
        self,
        *,
        is_processed: bool,
    ) -> Sequence[BrigadeErrorScreen]:
        return await self.get_list(
            QuerySpec(
                filters=(BrigadeErrorScreen.is_processed == is_processed,),
                order_by=(BrigadeErrorScreen.timestamp.desc(),),
            ),
        )

    async def list_unprocessed(self) -> Sequence[BrigadeErrorScreen]:
        return await self.list_by_processed_status(is_processed=False)

    async def list_by_timestamp_range(
        self,
        start_time: datetime,
        end_time: datetime,
    ) -> Sequence[BrigadeErrorScreen]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    BrigadeErrorScreen.timestamp >= to_aware(start_time),
                    BrigadeErrorScreen.timestamp <= to_aware(end_time),
                ),
                order_by=(BrigadeErrorScreen.timestamp.desc(),),
            ),
        )
