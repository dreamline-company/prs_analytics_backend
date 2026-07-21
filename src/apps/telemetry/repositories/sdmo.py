from collections.abc import Sequence
from datetime import date

from sqlalchemy import delete

from apps.telemetry.dto.internal.repositories.sdmo import (
    CreateSdmoFcDataDTO,
    CreateSdmoFcRegDTO,
    CreateSdmoStationDTO,
    UpdateSdmoFcDataDTO,
    UpdateSdmoFcRegDTO,
    UpdateSdmoStationDTO,
)
from apps.telemetry.models.sdmo import SdmoFcData, SdmoFcReg, SdmoStation
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class SdmoStationRepository(
    AsyncAlchemyRepository[CreateSdmoStationDTO, UpdateSdmoStationDTO, SdmoStation],
):
    model = SdmoStation

    async def delete_all(self) -> None:
        await self.session.execute(delete(SdmoStation))

    async def get_by_sdmo_id(self, sdmo_id: int) -> SdmoStation | None:
        return await self.get_one(
            QuerySpec(filters=(SdmoStation.sdmo_id == sdmo_id,)),
        )

    async def get_by_code(self, code: str) -> SdmoStation | None:
        return await self.get_one(
            QuerySpec(filters=(SdmoStation.code == code,)),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[SdmoStation]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoStation.well_id == well_id,),
                order_by=(SdmoStation.sdmo_id,),
            ),
        )


class SdmoFcRegRepository(
    AsyncAlchemyRepository[CreateSdmoFcRegDTO, UpdateSdmoFcRegDTO, SdmoFcReg],
):
    model = SdmoFcReg

    async def delete_all(self) -> None:
        await self.session.execute(delete(SdmoFcReg))

    async def get_by_addr(
        self,
        addr: int,
        type_1900: int | None = None,
    ) -> SdmoFcReg | None:
        filters = [SdmoFcReg.addr == addr]
        if type_1900 is not None:
            filters.append(SdmoFcReg.type_1900 == type_1900)

        return await self.get_one(QuerySpec(filters=tuple(filters)))

    async def list_all(self) -> Sequence[SdmoFcReg]:
        return await self.get_list(
            QuerySpec(order_by=(SdmoFcReg.type_1900, SdmoFcReg.addr)),
        )


class SdmoFcDataRepository(
    AsyncAlchemyRepository[CreateSdmoFcDataDTO, UpdateSdmoFcDataDTO, SdmoFcData],
):
    model = SdmoFcData

    async def get_last_sdmo_id(self) -> int:
        rows = await self.get_list(
            QuerySpec(
                order_by=(SdmoFcData.sdmo_id.desc(),),
                limit=1,
            ),
        )
        return rows[0].sdmo_id if rows else 0

    async def list_by_station(
        self,
        sdmo_station_id: int,
    ) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(SdmoFcData.sdmo_station_id == sdmo_station_id,),
                order_by=(SdmoFcData.day,),
            ),
        )

    async def list_by_station_period(
        self,
        sdmo_station_id: int,
        start_day: date,
        end_day: date,
    ) -> Sequence[SdmoFcData]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    SdmoFcData.sdmo_station_id == sdmo_station_id,
                    SdmoFcData.day >= start_day,
                    SdmoFcData.day <= end_day,
                ),
                order_by=(SdmoFcData.day,),
            ),
        )
