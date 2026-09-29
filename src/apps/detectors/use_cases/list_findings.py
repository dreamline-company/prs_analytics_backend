from apps.detectors.cits_events.incident_config import SECTION_KINDS
from apps.detectors.dto.internal.finding import DetectorFindingDTO
from apps.detectors.dto.queries.finding import ListFindingsQuery
from apps.detectors.repositories import DetectorFindingRepository
from apps.wells.repositories import WellRepository

SECTION_BY_KIND = {
    kind: section for section, kinds in SECTION_KINDS.items() for kind in kinds
}


class ListFindingsUseCase:
    """Суточный срез правила: «запросить замер» и качество данных."""

    def __init__(
        self,
        finding_repository: DetectorFindingRepository,
        well_repository: WellRepository,
    ) -> None:
        self.finding_repository = finding_repository
        self.well_repository = well_repository

    async def execute(self, query: ListFindingsQuery) -> list[DetectorFindingDTO]:
        fix_date = query.fix_date or await self.finding_repository.get_last_fix_date(
            query.detector_code,
        )
        if fix_date is None:
            return []

        kinds: list[str] | None = None
        if query.section is not None:
            kinds = list(SECTION_KINDS[query.section])
        if query.kind is not None:
            kinds = [kind for kind in kinds or [query.kind] if kind == query.kind]

        findings = await self.finding_repository.list_for_date(
            detector_code=query.detector_code,
            fix_date=fix_date,
            kinds=kinds,
            well_id=query.well_id,
        )
        if not findings:
            return []

        wells = await self.well_repository.list_by_ids(
            sorted({finding.well_id for finding in findings}),
        )
        names = {well.id: well.name for well in wells}

        result = []
        for finding in findings:
            dto = DetectorFindingDTO.model_validate(finding)
            dto.well_name = names.get(finding.well_id)
            dto.section = SECTION_BY_KIND.get(finding.kind)
            result.append(dto)
        return result
