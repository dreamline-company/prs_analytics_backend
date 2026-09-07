from apps.telemetry.dto.internal.sdmo import SdmoParametersDTO
from apps.telemetry.dto.queries.sdmo import ListSdmoParametersByWellIdQuery
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)


class ListSdmoParametersByWellIdUseCase:
    def __init__(
        self,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
    ) -> None:
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository

    async def execute(
        self,
        query: ListSdmoParametersByWellIdQuery,
    ) -> list[SdmoParametersDTO]:
        stations = await self.sdmo_station_repository.list_by_well_id(
            well_id=query.well_id,
        )
        if not stations:
            return []

        rows = await self.sdmo_fc_data_repository.list_parameters_by_stations_period(
            station_ids=[station.id for station in stations],
            start_time=query.start_time,
            end_time=query.end_time,
        )
        return [SdmoParametersDTO.model_validate(dict(row)) for row in rows]
