"""Матрица инцидентов по скважинам НГДУ.

Строка = скважина: способ эксплуатации, что видят детекторы прямо сейчас и
идёт ли ремонт. Всё считается батчами — на НГДУ приходятся сотни скважин, и
запрос на скважину превратил бы матрицу в сотни round-trip'ов. Необязательный
фильтр по месторождению применяется в ``NGDUWellsService`` до начала батчей.
"""

from apps.detectors.services import WellIncidentStatusService
from apps.repairs.services import CurrentRepairService
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository
from apps.telemetry.services import WellRatesService
from apps.wells.dto.internal.well_matrix_incidents import (
    WellMatrixIncidentDTO,
    WellMatrixIncidentExplDTO,
    WellMatrixIncidentPassportDTO,
    WellMatrixIncidentWellDTO,
)
from apps.wells.dto.queries.well import GetWellMatrixIncidentsQuery
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.services import NGDUWellsService


class GetWellMatrixIncidentsUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        ngdu_wells_service: NGDUWellsService,
        well_expl_repository: WellExplRepository,
        well_incident_status_service: WellIncidentStatusService,
        current_repair_service: CurrentRepairService,
        well_rates_service: WellRatesService,
        sdmo_fc_data_repository: SdmoFcDataRepository,
    ) -> None:
        self.ngdu_wells_service = ngdu_wells_service
        self.well_expl_repository = well_expl_repository
        self.well_incident_status_service = well_incident_status_service
        self.current_repair_service = current_repair_service
        self.well_rates_service = well_rates_service
        self.sdmo_fc_data_repository = sdmo_fc_data_repository

    async def execute(
        self,
        query: GetWellMatrixIncidentsQuery,
    ) -> list[WellMatrixIncidentDTO]:
        wells = await self.ngdu_wells_service.list_wells(
            query.ngdu_id,
            oil_field_id=query.oil_field_id,
        )
        if not wells:
            return []

        abai_well_ids = [well.abai_id for well in wells]
        expl_names = (
            await self.well_expl_repository.get_latest_expl_name_by_abai_well_ids(
                abai_well_ids,
            )
        )
        # Детекторы ведут эпизоды по локальному well_id, ремонты приезжают из
        # ABAI и связываются по abai_well_id — отсюда два разных ключа.
        incident_statuses = await self.well_incident_status_service.get_for_wells(
            [well.id for well in wells],
        )
        current_repairs = await self.current_repair_service.get_for_wells(
            abai_well_ids,
        )
        rates = await self.well_rates_service.get_for_wells(
            {well.id: well.abai_id for well in wells},
        )
        # Станции СДМО привязаны по локальному well_id, как и телеметрия.
        well_ids = [well.id for well in wells]
        fc_data_repository = self.sdmo_fc_data_repository
        sdmo_times = await fc_data_repository.get_last_savetime_by_well_ids(well_ids)
        vlt_statuses = await fc_data_repository.get_last_vlt_status_by_well_ids(
            well_ids,
        )

        return [
            WellMatrixIncidentDTO(
                well=WellMatrixIncidentWellDTO(id=well.id, well_name=well.name),
                expl=(
                    WellMatrixIncidentExplDTO(name_ru=expl_names[well.abai_id])
                    if well.abai_id in expl_names
                    else None
                ),
                incident_status=incident_statuses[well.id],
                current_repair=current_repairs.get(well.abai_id),
                passport=WellMatrixIncidentPassportDTO(
                    **rates[well.id].model_dump(),
                    sdmo_time=sdmo_times.get(well.id),
                    sdmo_vlt_status=vlt_statuses.get(well.id),
                ),
            )
            for well in sorted(wells, key=lambda well: well.name)
        ]
