from apps.detectors.dto.internal.incident import DetectorIncidentDTO
from apps.detectors.dto.queries.incident import ListIncidentsByWellIdQuery
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
)
from shared.repository.sqlalchemy import QuerySpec


class ListIncidentsByWellIdUseCase:
    """Выгрузка эпизодов детекции по скважине."""

    def __init__(
        self,
        incident_repository: DetectorIncidentRepository,
        detector_repository: DetectorRepository,
    ) -> None:
        self.incident_repository = incident_repository
        self.detector_repository = detector_repository

    async def execute(
        self,
        query: ListIncidentsByWellIdQuery,
    ) -> list[DetectorIncidentDTO]:
        incidents = await self.incident_repository.list_by_well_id(
            well_id=query.well_id,
            detector_code=query.detector_code,
            reason_code=query.reason_code,
            status=query.status,
            level=query.level,
            opened_from=query.opened_from,
            opened_to=query.opened_to,
            limit=query.limit,
            offset=query.offset,
        )
        if not incidents:
            return []

        # Реестр правил — единицы строк, тянется целиком вместо join на каждую
        # выгрузку.
        detectors = await self.detector_repository.get_list(QuerySpec())
        names = {detector.code: detector.name_ru for detector in detectors}

        result = []
        for incident in incidents:
            dto = DetectorIncidentDTO.model_validate(incident)
            dto.detector_name_ru = names.get(incident.detector_code)
            result.append(dto)
        return result
