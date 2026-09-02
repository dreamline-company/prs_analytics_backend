"""Актуальное ИИ-заключение скважины: по активным эпизодам R2/R9."""

from apps.detectors.conclusion.catalog import CONCLUSION_DETECTOR_CODES
from apps.detectors.dto.internal.conclusion import (
    DetectorConclusionDTO,
    WellAiConclusionDTO,
)
from apps.detectors.models.conclusion import DetectorConclusion
from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    DetectorIncident,
)
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorIncidentRepository,
    DetectorRepository,
)
from shared.repository.sqlalchemy import QuerySpec


class GetWellAiConclusionUseCase:
    """Заключения активных эпизодов; главное — у худшего уровня.

    На эпизод отдаётся заключение под его ТЕКУЩИЙ уровень; пока оно pending
    или failed, фронт показывает состояние генерации. Если alarm-заключение
    ещё не готово, а warning уже есть — отдаётся warning (лучше вчерашняя
    интерпретация, чем пустота), уровень эпизода виден в поле level эпизода.
    """

    def __init__(
        self,
        *,
        incident_repository: DetectorIncidentRepository,
        conclusion_repository: DetectorConclusionRepository,
        detector_repository: DetectorRepository,
    ) -> None:
        self.incident_repository = incident_repository
        self.conclusion_repository = conclusion_repository
        self.detector_repository = detector_repository

    async def execute(self, well_id: int) -> WellAiConclusionDTO:
        incidents = [
            incident
            for incident in await self.incident_repository.list_active_by_well_id(
                well_id,
            )
            if incident.detector_code in CONCLUSION_DETECTOR_CODES
        ]
        if not incidents:
            return WellAiConclusionDTO(primary=None, others=[])

        conclusions = await self.conclusion_repository.list_by_incident_ids(
            [incident.id for incident in incidents],
            status=None,
        )
        by_incident: dict[int, list[DetectorConclusion]] = {}
        for conclusion in conclusions:
            by_incident.setdefault(conclusion.incident_id, []).append(conclusion)

        names = await self._detector_names()
        picked: list[tuple[DetectorIncident, DetectorConclusionDTO]] = []
        for incident in incidents:
            row = self._pick(incident, by_incident.get(incident.id, []))
            if row is None:
                continue
            dto = DetectorConclusionDTO.model_validate(row)
            dto.detector_name_ru = names.get(row.detector_code)
            picked.append((incident, dto))

        if not picked:
            return WellAiConclusionDTO(primary=None, others=[])

        picked.sort(key=self._rank)
        return WellAiConclusionDTO(
            primary=picked[0][1],
            others=[dto for _, dto in picked[1:]],
        )

    @staticmethod
    def _pick(
        incident: DetectorIncident,
        rows: list[DetectorConclusion],
    ) -> DetectorConclusion | None:
        """Заключение эпизода: под текущий уровень, иначе — самое свежее."""
        exact = [row for row in rows if row.level == incident.level]
        pool = exact or rows
        if not pool:
            return None
        return max(pool, key=lambda row: row.id)

    @staticmethod
    def _rank(item: tuple[DetectorIncident, DetectorConclusionDTO]) -> tuple:
        incident, _ = item
        is_alarm = incident.level == INCIDENT_LEVEL_ALARM
        # Сначала alarm'ы, внутри уровня — более свежий эпизод.
        return (0 if is_alarm else 1, -incident.opened_at.timestamp())

    async def _detector_names(self) -> dict[str, str]:
        detectors = await self.detector_repository.get_list(QuerySpec())
        return {detector.code: detector.name_ru for detector in detectors}
