from collections.abc import Sequence

from apps.telemetry.dto.internal.repositories.telemetry import (
    CreateTelemetryDTO,
    UpdateTelemetryDTO,
)
from apps.telemetry.models.telemetry import Telemetry
from shared.repository.base import AsyncAlchemyRepository, QuerySpec


class TelemetryRepository(
    AsyncAlchemyRepository[CreateTelemetryDTO, UpdateTelemetryDTO, Telemetry],
):
    model = Telemetry

    async def get_by_id(self, telemetry_id: int) -> Telemetry | None:
        return await self.get_one(
            QuerySpec(
                filters=(Telemetry.id == telemetry_id,),
            ),
        )

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
