from apps.detectors.dto.internal.incident import (
    DetectorIncidentDTO,
    IncidentVerificationDTO,
)
from apps.detectors.dto.queries.incident import ListIncidentsByWellIdQuery
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from shared.repository.sqlalchemy import QuerySpec


class ListIncidentsByWellIdUseCase:
    """Выгрузка эпизодов детекции по скважине вместе с отметкой проверки."""

    def __init__(
        self,
        incident_repository: DetectorIncidentRepository,
        detector_repository: DetectorRepository,
        verification_repository: DetectorVerificationRepository,
    ) -> None:
        self.incident_repository = incident_repository
        self.detector_repository = detector_repository
        self.verification_repository = verification_repository

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
            verdict=query.verdict,
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
        verifications = await self.verification_repository.get_map_by_incident_ids(
            [incident.id for incident in incidents],
        )

        result = []
        for incident in incidents:
            dto = DetectorIncidentDTO.model_validate(incident)
            dto.detector_name_ru = names.get(incident.detector_code)
            verification = verifications.get(incident.id)
            if verification is not None:
                dto.verification = IncidentVerificationDTO.model_validate(verification)
            result.append(dto)
        return result
