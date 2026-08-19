"""Карточка скважины: статус, устройство и паспорт по последним отсчётам."""

from collections.abc import Sequence

from starlette import status

from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.wells.dto.internal.well_card import (
    WellCardDTO,
    WellCardPassportDTO,
    WellCardStatusDTO,
)
from apps.wells.dto.queries.well import GetWellCardQuery
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.services import CoordPointService
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
        telemetry_repository: TelemetryRepository,
        tech_regime_repository: TechRegimeRepository,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
        well_status_history_repository: WellStatusHistoryRepository,
        coord_point_service: CoordPointService,
    ) -> None:
        self.well_repository = well_repository
        self.telemetry_repository = telemetry_repository
        self.tech_regime_repository = tech_regime_repository
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository
        self.well_status_history_repository = well_status_history_repository
        self.coord_point_service = coord_point_service

    async def execute(self, query: GetWellCardQuery) -> WellCardDTO:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": query.well_id})

        telemetry = await self.telemetry_repository.get_last_by_well_id(
            well_id=well.id,
        )
        tech_regime = await self.tech_regime_repository.get_last_by_abai_well_id(
            abai_well_id=well.abai_id,
        )
        stations = await self.sdmo_station_repository.list_by_well_id(well_id=well.id)
        pump = None
        if stations:
            fc_data_repository = self.sdmo_fc_data_repository
            pump = await fc_data_repository.get_last_pump_parameters_by_stations(
                station_sdmo_ids=[station.sdmo_id for station in stations],
            )
        last_status = await self.well_status_history_repository.get_last_by_well_id(
            well_id=well.id,
        )
        coord = await self.coord_point_service.resolve(well.coords_id)

        oil_rate = telemetry.qm_oil if telemetry else None
        liquid_rate = telemetry.qv_liquid if telemetry else None

        return WellCardDTO(
            well_id=well.id,
            well_name=well.name,
            device=self._device(stations),
            status=(
                WellCardStatusDTO.model_validate(last_status) if last_status else None
            ),
            coord=coord,
            passport=WellCardPassportDTO(
                oil_rate=oil_rate,
                liquid_rate=liquid_rate,
                water_cut=self._water_cut(liquid_rate=liquid_rate, oil_rate=oil_rate),
                plan_oil_rate=tech_regime.oil if tech_regime else None,
                pump_moment=pump["pump_moment"] if pump else None,
                pump_speed=pump["pump_speed"] if pump else None,
                pump_fill=pump["pump_fill"] if pump else None,
                zero_rate_days=ZERO_RATE_DAYS_STUB,
            ),
        )

    @staticmethod
    def _device(stations: Sequence[SdmoStation]) -> str | None:
        """Устройство скважины — серийник привязанной станции СДМО (СУ)."""
        for station in stations:
            if station.serial_number:
                return station.serial_number
        return None

    @staticmethod
    def _water_cut(
        *,
        liquid_rate: float | None,
        oil_rate: float | None,
    ) -> float | None:
        """Обводнённость, % = доля воды в жидкости: (жидкость - нефть) / жидкость."""
        if liquid_rate is None or oil_rate is None or liquid_rate <= 0:
            return None

        water_cut = (liquid_rate - oil_rate) / liquid_rate * 100
        return round(min(max(water_cut, 0.0), 100.0), 1)
