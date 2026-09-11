from apps.telemetry.dto.internal.sdmo import SdmoParametersDTO
from apps.telemetry.dto.queries.sdmo import ListSdmoParametersByWellIdQuery
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.services.sdmo_scale import (
    SERIES_PARAMETER_REGISTERS,
    SdmoRegisterScaler,
)


class ListSdmoParametersByWellIdUseCase:
    def __init__(
        self,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        sdmo_fc_reg_repository: SdmoFcRegRepository,
    ) -> None:
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.sdmo_fc_reg_repository = sdmo_fc_reg_repository

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
        if not rows:
            return []

        # koef берётся по типу станции каждого отсчёта: у скважины может быть
        # несколько станций разных типов.
        type_by_station = {station.id: station.type_1900 for station in stations}
        scaler = await SdmoRegisterScaler.load(self.sdmo_fc_reg_repository)
        result = []
        for row in rows:
            scaled = scaler.scale_row(
                row,
                type_1900=type_by_station.get(row["station_id"]),
                registers=SERIES_PARAMETER_REGISTERS,
            )
            result.append(
                SdmoParametersDTO(
                    savetime=row["savetime"],
                    scaled=scaled.scaled,
                    **scaled.values,
                ),
            )
        return result
