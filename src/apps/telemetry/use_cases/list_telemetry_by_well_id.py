from apps.telemetry.dto.internal.telemetry import TelemetryDTO
from apps.telemetry.dto.queries.telemetry import ListTelemetryByWellIdQuery
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.repositories import WellRepository


class ListTelemetryByWellIdUseCase:
    def __init__(
        self,
        telemetry_repository: TelemetryRepository,
        well_repository: WellRepository,
    ) -> None:
        self.telemetry_repository = telemetry_repository
        self.well_repository = well_repository

    async def execute(
        self,
        query: ListTelemetryByWellIdQuery,
    ) -> list[TelemetryDTO]:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if not well:
            return []

        telemetry = await self.telemetry_repository.list_by_well_id_in_period(
            well_id=well.id,
            date_time_from=query.date_time_from,
            date_time_to=query.date_time_to,
        )
        return [TelemetryDTO.model_validate(item) for item in telemetry]
