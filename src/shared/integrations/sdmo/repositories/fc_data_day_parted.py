from collections.abc import Sequence
from datetime import date

from shared.integrations.sdmo.models import FcDataDayParted
from shared.integrations.sdmo.repositories.base import SDMOReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class SDMOFcDataDayPartedRepository(
    SDMOReadOnlyRepository[FcDataDayParted],
):
    model = FcDataDayParted

    async def list_by_station(
        self,
        station_id: int,
    ) -> Sequence[FcDataDayParted]:
        return await self.get_list(
            QuerySpec(
                filters=(FcDataDayParted.station_id == station_id,),
                order_by=(FcDataDayParted.day,),
            ),
        )

    async def list_by_station_period(
        self,
        station_id: int,
        start_day: date,
        end_day: date,
    ) -> Sequence[FcDataDayParted]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    FcDataDayParted.station_id == station_id,
                    FcDataDayParted.day >= start_day,
                    FcDataDayParted.day <= end_day,
                ),
                order_by=(FcDataDayParted.day,),
            ),
        )

    async def list_by_stations_period(
        self,
        station_ids: Sequence[int],
        start_day: date,
        end_day: date,
    ) -> Sequence[FcDataDayParted]:
        if not station_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(
                    FcDataDayParted.station_id.in_(station_ids),
                    FcDataDayParted.day >= start_day,
                    FcDataDayParted.day <= end_day,
                ),
                order_by=(FcDataDayParted.station_id, FcDataDayParted.day),
            ),
        )

    async def get_by_station_day(
        self,
        station_id: int,
        day: date,
    ) -> FcDataDayParted | None:
        return await self.get_one(
            QuerySpec(
                filters=(
                    FcDataDayParted.station_id == station_id,
                    FcDataDayParted.day == day,
                ),
            ),
        )
