from datetime import datetime

from pydantic import BaseModel


class WellStateDTO(BaseModel):
    """Итоговое состояние скважины — одно на матрицу и карточку.

    ``code``: working / warning / alarm / stopped / repair / no_link /
    inactive. ``since`` — начало состояния по данным (местное время), а не
    время последнего отсчёта. ``stale`` — состояние может быть неактуально,
    почему — в ``stale_reason`` (таска не обновляла его дольше 15 минут или
    идёт сбой СДМО по НГДУ станции).
    """

    code: str
    title_ru: str
    reason_ru: str
    since: datetime
    source: str  # sdmo / abai / repair / detector
    stale: bool = False
    stale_reason: str | None = None
