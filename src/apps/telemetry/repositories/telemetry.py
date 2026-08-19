from collections.abc import Sequence
from datetime import datetime

from apps.telemetry.dto.internal.repositories.telemetry import (
    CreateTelemetryDTO,
    UpdateTelemetryDTO,
)
from apps.telemetry.models.telemetry import Telemetry
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec


class TelemetryRepository(
    AsyncAlchemyRepository[CreateTelemetryDTO, UpdateTelemetryDTO, Telemetry],
):
    model = Telemetry

    async def get_by_abai_id(self, abai_id: int) -> Telemetry | None:
        return await self.get_one(
            QuerySpec(
                filters=(Telemetry.abai_id == abai_id,),
            ),
        )

    async def list_by_well_id(self, well_id: int) -> Sequence[Telemetry]:
        return await self.get_list(
            QuerySpec(
                filters=(Telemetry.well_id == well_id,),
                order_by=(Telemetry.date_time,),
            ),
        )

    async def list_by_well_id_in_period(
        self,
        well_id: int,
        *,
        date_time_from: datetime | None = None,
        date_time_to: datetime | None = None,
    ) -> Sequence[Telemetry]:
        filters = [Telemetry.well_id == well_id]
        if date_time_from is not None:
            filters.append(Telemetry.date_time >= date_time_from)
        if date_time_to is not None:
            filters.append(Telemetry.date_time <= date_time_to)

        return await self.get_list(
            QuerySpec(
                filters=tuple(filters),
                order_by=(Telemetry.date_time.asc(),),
            ),
        )

    async def update_by_id(
        self,
        telemetry_id: int,
        data: UpdateTelemetryDTO,
    ) -> Telemetry:
        return await self.update(
            data=data,
            filters=(Telemetry.id == telemetry_id,),
        )

    async def delete_by_id(self, telemetry_id: int) -> None:
        await self.delete(filters=(Telemetry.id == telemetry_id,))

    async def get_last_by_well_id(self, well_id: int) -> Telemetry | None:
        tms = await self.get_list(
            QuerySpec(
                filters=(Telemetry.well_id == well_id,),
                order_by=(Telemetry.date_time.desc(),),
                limit=1,
            ),
        )
        return tms[0] if tms else None

    async def get_last_by_ngdu_id(self, abai_ngdu_id: int) -> Telemetry | None:
        tms = await self.get_list(
            QuerySpec(
                filters=(Telemetry.abai_ngdu_id == abai_ngdu_id,),
                order_by=(Telemetry.date_time.desc(),),
                limit=1,
            ),
        )
        return tms[0] if tms else None
