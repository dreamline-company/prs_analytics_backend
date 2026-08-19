"""Матрица инцидентов по скважинам НГДУ.

Пока в строке только сама скважина и текущий способ эксплуатации — состав
инцидентов добавится сюда же.
"""

from apps.wells.dto.internal.well_matrix_incidents import (
    WellMatrixIncidentDTO,
    WellMatrixIncidentExplDTO,
    WellMatrixIncidentWellDTO,
)
from apps.wells.dto.queries.well import GetWellMatrixIncidentsQuery
from apps.wells.repositories.well_expl import WellExplRepository
from apps.wells.services import NGDUWellsService


class GetWellMatrixIncidentsUseCase:
    def __init__(
        self,
        *,
        ngdu_wells_service: NGDUWellsService,
        well_expl_repository: WellExplRepository,
    ) -> None:
        self.ngdu_wells_service = ngdu_wells_service
        self.well_expl_repository = well_expl_repository

    async def execute(
        self,
        query: GetWellMatrixIncidentsQuery,
    ) -> list[WellMatrixIncidentDTO]:
        wells = await self.ngdu_wells_service.list_wells(query.ngdu_id)
        if not wells:
            return []

        expl_names = (
            await self.well_expl_repository.get_latest_expl_name_by_abai_well_ids(
                [well.abai_id for well in wells],
            )
        )

        return [
            WellMatrixIncidentDTO(
                well=WellMatrixIncidentWellDTO(id=well.id, well_name=well.name),
                expl=(
                    WellMatrixIncidentExplDTO(name_ru=expl_names[well.abai_id])
                    if well.abai_id in expl_names
                    else None
                ),
            )
            for well in sorted(wells, key=lambda well: well.name)
        ]
