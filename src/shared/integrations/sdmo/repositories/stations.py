from collections.abc import Sequence

from shared.integrations.sdmo.models import Station
from shared.integrations.sdmo.repositories.base import SDMOReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class SDMOStationRepository(
    SDMOReadOnlyRepository[Station],
):
    model = Station

    async def get_by_code(self, code: str) -> Station | None:
        return await self.get_one(
            QuerySpec(
                filters=(Station.code == code,),
            ),
        )

    async def get_by_serial_number(self, serial_number: str) -> Station | None:
        return await self.get_one(
            QuerySpec(
                filters=(Station.serial_number == serial_number,),
            ),
        )

    async def list_by_ids(self, station_ids: Sequence[int]) -> Sequence[Station]:
        if not station_ids:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(Station.id.in_(station_ids),),
                order_by=(Station.id,),
            ),
        )

    async def list_by_place(self, place_id: int) -> Sequence[Station]:
        return await self.get_list(
            QuerySpec(
                filters=(Station.place_id == place_id,),
                order_by=(Station.id,),
            ),
        )

    async def list_active(self) -> Sequence[Station]:
        return await self.get_list(
            QuerySpec(
                filters=(Station.active.is_(True),),
                order_by=(Station.id,),
            ),
        )
