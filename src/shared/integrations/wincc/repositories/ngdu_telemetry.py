from collections.abc import Sequence
from datetime import datetime

from shared.integrations.wincc.models import NGDUWinccTelemetryModel
from shared.integrations.wincc.repositories.base import WinccReadOnlyRepository
from shared.repository.sqlalchemy import QuerySpec


class NGDUWinccTelemetryRepository[ModelT: NGDUWinccTelemetryModel](
    WinccReadOnlyRepository[ModelT],
):
    async def list_by_well(self, well: str) -> Sequence[ModelT]:
        return await self.get_list(
            QuerySpec(
                filters=(self.model.Well == well,),
                order_by=(self.model.Meas_date,),
            ),
        )

    async def list_by_wells(self, wells: Sequence[str]) -> Sequence[ModelT]:
        if not wells:
            return ()

        return await self.get_list(
            QuerySpec(
                filters=(self.model.Well.in_(wells),),
                order_by=(self.model.Well, self.model.Meas_date),
            ),
        )

    async def list_by_oil_field(self, oil_field: str) -> Sequence[ModelT]:
        return await self.get_list(
            QuerySpec(
                filters=(self.model.Oil_field == oil_field,),
                order_by=(self.model.Well, self.model.Meas_date),
            ),
        )

    async def list_by_period(
        self,
        start_date: datetime,
        end_date: datetime,
    ) -> Sequence[ModelT]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    self.model.Meas_date >= start_date,
                    self.model.Meas_date <= end_date,
                ),
                order_by=(self.model.Meas_date,),
            ),
        )

    async def list_by_well_period(
        self,
        well: str,
        start_date: datetime,
        end_date: datetime,
    ) -> Sequence[ModelT]:
        return await self.get_list(
            QuerySpec(
                filters=(
                    self.model.Well == well,
                    self.model.Meas_date >= start_date,
                    self.model.Meas_date <= end_date,
                ),
                order_by=(self.model.Meas_date,),
            ),
        )
