"""Свёртка активных эпизодов скважины в один статус для карточки.

Таблица инцидентов хранит по строке на сигнал: одно физическое событие может
дать несколько эпизодов (момент, скорость, заполнение), у каждого свой
таймлайн. «Состояние скважины» — производная величина и считается на чтении,
здесь.
"""

from collections import defaultdict
from collections.abc import Sequence

from apps.detectors.dto.internal.well_status import (
    ActiveIncidentDTO,
    WellIncidentStatusDTO,
)
from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    DetectorIncident,
)
from apps.detectors.repositories import (
    DetectorIncidentRepository,
    DetectorRepository,
)
from shared.repository.sqlalchemy import QuerySpec

# Уровень «активных эпизодов нет». В таблице инцидентов такого уровня не
# существует — это состояние отсутствия строк, и живёт оно только на чтении.
WELL_STATE_NORMAL = "normal"

NORMAL_TITLE_RU = "Работает в штатном режиме"
UNKNOWN_TITLE_RU = "Обнаружено нарушение"

# Порядок серьёзности: чем больше, тем хуже. Неизвестный уровень (правило
# завели новее этого кода) не должен маскироваться под норму.
LEVEL_RANK = {
    WELL_STATE_NORMAL: 0,
    INCIDENT_LEVEL_WARNING: 1,
    INCIDENT_LEVEL_ALARM: 2,
}
UNKNOWN_LEVEL_RANK = 3


def _rank(level: str) -> int:
    return LEVEL_RANK.get(level, UNKNOWN_LEVEL_RANK)


def build_well_incident_status(
    incidents: Sequence[DetectorIncident],
    detector_names: dict[str, str],
) -> WellIncidentStatusDTO:
    """Собрать статус из активных эпизодов скважины.

    Чистая функция — вся выборка снаружи, чтобы логику свёртки можно было
    проверить без базы.
    """
    if not incidents:
        return WellIncidentStatusDTO(
            level=WELL_STATE_NORMAL,
            title_ru=NORMAL_TITLE_RU,
            since=None,
            incidents=[],
        )

    # Худший уровень первым, внутри уровня — тот, что начался раньше: он и
    # попадает в шапку.
    ordered = sorted(
        incidents,
        key=lambda incident: (-_rank(incident.level), incident.opened_at),
    )
    items = []
    for incident in ordered:
        item = ActiveIncidentDTO.model_validate(incident)
        item.detector_name_ru = detector_names.get(incident.detector_code)
        items.append(item)

    worst = items[0]
    return WellIncidentStatusDTO(
        level=worst.level,
        title_ru=worst.detector_name_ru or UNKNOWN_TITLE_RU,
        since=worst.opened_at,
        incidents=items,
    )


class WellIncidentStatusService:
    def __init__(
        self,
        incident_repository: DetectorIncidentRepository,
        detector_repository: DetectorRepository,
    ) -> None:
        self.incident_repository = incident_repository
        self.detector_repository = detector_repository

    async def get_for_well(self, well_id: int) -> WellIncidentStatusDTO:
        statuses = await self.get_for_wells([well_id])
        return statuses[well_id]

    async def get_for_wells(
        self,
        well_ids: Sequence[int],
    ) -> dict[int, WellIncidentStatusDTO]:
        """Статус на каждую запрошенную скважину.

        В ответе есть ключ для любой запрошенной скважины: отсутствие
        эпизодов — это тоже статус («работает в штатном режиме»), и вызывающему
        не должно приходиться отличать «нет ключа» от «нет аварий».
        """
        incidents = await self.incident_repository.list_active_by_well_ids(well_ids)

        names: dict[str, str] = {}
        if incidents:
            # Реестр правил — единицы строк, тянется целиком вместо join.
            detectors = await self.detector_repository.get_list(QuerySpec())
            names = {detector.code: detector.name_ru for detector in detectors}

        by_well: dict[int, list[DetectorIncident]] = defaultdict(list)
        for incident in incidents:
            by_well[incident.well_id].append(incident)

        return {
            well_id: build_well_incident_status(by_well.get(well_id, []), names)
            for well_id in well_ids
        }
