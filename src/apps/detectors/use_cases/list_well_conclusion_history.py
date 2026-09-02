"""История ИИ-заключений скважины — лента для иконки «история» в карточке."""

from apps.detectors.dto.internal.conclusion import DetectorConclusionDTO
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorRepository,
)
from shared.repository.sqlalchemy import QuerySpec


class ListWellConclusionHistoryUseCase:
    def __init__(
        self,
        *,
        conclusion_repository: DetectorConclusionRepository,
        detector_repository: DetectorRepository,
    ) -> None:
        self.conclusion_repository = conclusion_repository
        self.detector_repository = detector_repository

    async def execute(
        self,
        *,
        well_id: int,
        limit: int,
        offset: int,
    ) -> list[DetectorConclusionDTO]:
        rows = await self.conclusion_repository.list_by_well_id(
            well_id=well_id,
            limit=limit,
            offset=offset,
        )
        if not rows:
            return []

        detectors = await self.detector_repository.get_list(QuerySpec())
        names = {detector.code: detector.name_ru for detector in detectors}

        result = []
        for row in rows:
            dto = DetectorConclusionDTO.model_validate(row)
            dto.detector_name_ru = names.get(row.detector_code)
            result.append(dto)
        return result
