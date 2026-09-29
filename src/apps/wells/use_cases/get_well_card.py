"""Карточка скважины: статус, устройство и паспорт по последним отсчётам."""

from collections.abc import Sequence

from starlette import status

from apps.detectors.services import WellIncidentStatusService
from apps.repairs.services import CurrentRepairService
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import (
    ROTOR_SPEED_REGISTER,
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.services import (
    PUMP_PARAMETER_REGISTERS,
    SdmoRegisterScaler,
    WellRatesService,
)
from apps.wells.dto.internal.well_card import (
    WellCardDTO,
    WellCardPassportDTO,
    WellCardStatusDTO,
)
from apps.wells.dto.queries.well import GetWellCardQuery
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.services import CoordPointService, WellGdisService
from shared.errors import HttpError

# «Дней с дебитом 0» пока не считается — источника нет, отдаём 0.
ZERO_RATE_DAYS_STUB = 0


class WellNotFoundError(HttpError):
    message = "Well not found."
    code = "well_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetWellCardUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        well_repository: WellRepository,
        well_rates_service: WellRatesService,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        sdmo_fc_reg_repository: SdmoFcRegRepository,
        well_status_history_repository: WellStatusHistoryRepository,
        coord_point_service: CoordPointService,
        well_incident_status_service: WellIncidentStatusService,
        current_repair_service: CurrentRepairService,
        well_gdis_service: WellGdisService,
    ) -> None:
        self.well_repository = well_repository
        self.well_rates_service = well_rates_service
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.sdmo_fc_reg_repository = sdmo_fc_reg_repository
        self.well_status_history_repository = well_status_history_repository
        self.coord_point_service = coord_point_service
        self.well_incident_status_service = well_incident_status_service
        self.current_repair_service = current_repair_service
        self.well_gdis_service = well_gdis_service

    async def execute(self, query: GetWellCardQuery) -> WellCardDTO:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": query.well_id})

        rates = await self.well_rates_service.get_for_well(
            well_id=well.id,
            abai_well_id=well.abai_id,
        )
        stations = await self.sdmo_station_repository.list_by_well_id(well_id=well.id)
        pump = None
        pump_values: dict[str, float | None] = {}
        pump_scaled = False
        speed_units = None
        vlt_status = None
        vlt_status_time = None
        if stations:
            fc_data_repository = self.sdmo_fc_data_repository
            pump = await fc_data_repository.get_last_pump_parameters_by_stations(
                station_ids=[station.id for station in stations],
            )
            if pump is not None:
                # koef справочника по типу станции отсчёта; без типа — сырое
                # с sdmo_scaled=False.
                station_type = self._station_type(stations, pump["station_id"])
                scaler = await SdmoRegisterScaler.load(self.sdmo_fc_reg_repository)
                scaled = scaler.scale_row(
                    pump,
                    type_1900=station_type,
                    registers=PUMP_PARAMETER_REGISTERS,
                )
                pump_values, pump_scaled = scaled.values, scaled.scaled
                speed_units = scaler.units(
                    addr=ROTOR_SPEED_REGISTER,
                    type_1900=station_type,
                )
            # Тем же запросом, что и матрица: строка матрицы и карточка должны
            # показывать один и тот же статус станции.
            vlt_statuses = (
                await fc_data_repository.get_last_vlt_status_with_time_by_well_ids(
                    [well.id],
                )
            )
            if well.id in vlt_statuses:
                vlt_status, vlt_status_time = vlt_statuses[well.id]
        last_status = await self.well_status_history_repository.get_last_by_well_id(
            well_id=well.id,
        )
        coord = await self.coord_point_service.resolve(well.coords_id)
        incident_status = await self.well_incident_status_service.get_for_well(well.id)
        current_repair = await self.current_repair_service.get_for_well(well.abai_id)
        dynamic_level = await self.well_gdis_service.get_last_dynamic_level(
            well.abai_id,
        )

        return WellCardDTO(
            well_id=well.id,
            well_name=well.name,
            device=self._device(stations),
            status=(
                WellCardStatusDTO.model_validate(last_status) if last_status else None
            ),
            incident_status=incident_status,
            current_repair=current_repair,
            coord=coord,
            passport=WellCardPassportDTO(
                **rates.model_dump(),
                pump_moment=pump_values.get("pump_moment"),
                pump_speed=pump_values.get("pump_speed"),
                pump_speed_units=speed_units,
                pump_fill=pump_values.get("pump_fill"),
                sdmo_time=pump["savetime"] if pump else None,
                sdmo_scaled=pump_scaled,
                sdmo_vlt_status=vlt_status,
                sdmo_vlt_status_time=vlt_status_time,
                h_din_m=dynamic_level.h_din_m if dynamic_level else None,
                h_din_date=dynamic_level.meas_date if dynamic_level else None,
                zero_rate_days=ZERO_RATE_DAYS_STUB,
            ),
        )

    @staticmethod
    def _station_type(stations: Sequence[SdmoStation], station_id: int) -> int | None:
        return next(
            (station.type_1900 for station in stations if station.id == station_id),
            None,
        )

    @staticmethod
    def _device(stations: Sequence[SdmoStation]) -> str | None:
        """Устройство скважины — серийник привязанной станции СДМО (СУ)."""
        for station in stations:
            if station.serial_number:
                return station.serial_number
        return None
