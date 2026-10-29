from datetime import date, datetime

from pydantic import BaseModel

from apps.wells.dto.internal.well_state import WellStateDTO

MARK_OK = "ok"
MARK_ATTENTION = "attention"
MARK_INFO = "info"


class WellOverviewItemDTO(BaseModel):
    """Строка сводки: блок, текст для оператора и отметка.

    ``code``: work / production / production_trend / drive / dynamogram / gdis /
    repairs / detectors / compensation. ``mark``: ok — норма, attention —
    обратить внимание, info — справка без оценки. ``as_of`` — дата данных
    строки (замер, исследование), если она есть.
    """

    code: str
    title_ru: str
    text_ru: str
    mark: str
    as_of: datetime | date | None = None


class WellOverviewObservationDTO(BaseModel):
    """Наблюдение: мягкий сигнал ниже порогов детекторов — за чем следить."""

    code: str
    text_ru: str


class WellOverviewDTO(BaseModel):
    well_id: int
    well_name: str
    as_of: datetime  # местное время сборки сводки
    # Итоговое состояние скважины — тот же блок, что в матрице и карточке.
    state: WellStateDTO | None
    items: list[WellOverviewItemDTO]
    observations: list[WellOverviewObservationDTO]
