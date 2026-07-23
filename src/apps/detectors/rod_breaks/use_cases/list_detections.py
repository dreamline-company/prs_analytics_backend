from apps.detectors.rod_breaks.dto.queries.detection import (
    ListRodBreakDetectionsQuery,
)
from apps.detectors.rod_breaks.dto.responses.detection import (
    RodBreakDetectionReadDTO,
)
from apps.detectors.rod_breaks.repositories.detection import (
    RodBreakDetectionRepository,
)


class ListRodBreakDetections:
    """Отдать сохранённые детекции обрыва штанги с фильтрами."""

    def __init__(self, repository: RodBreakDetectionRepository) -> None:
        self.repository = repository

    async def execute(
        self,
        query: ListRodBreakDetectionsQuery,
    ) -> list[RodBreakDetectionReadDTO]:
        rows = await self.repository.list_filtered(
            query.well_id,
            only_fired=query.only_fired,
            limit=query.limit,
        )
        return [RodBreakDetectionReadDTO.model_validate(row) for row in rows]
