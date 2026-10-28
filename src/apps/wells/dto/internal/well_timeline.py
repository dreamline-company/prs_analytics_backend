"""Хронология событий скважины: случаи «сигнал → поломка → начало ремонта».

Отсутствующий узел — ``None``: сигнала не было, отдельного простоя перед
ремонтом в ABAI нет, ремонт не начат. Времена — местные.
"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel


class WellTimelineSignalDTO(BaseModel):
    """Эпизод детектора: когда открылся и чем закончился."""

    at: datetime
    incident_id: int
    detector_code: str
    title: str
    reason_code: str
    level: str
    status: str
    # Отметка проверки (пока только у R2); None — не проверялся.
    verdict: str | None


class WellTimelineBreakdownDTO(BaseModel):
    """Начало простоя-отказа в ABAI."""

    at: datetime
    status_code: str | None
    title: str


class WellTimelineRepairDTO(BaseModel):
    """Начало ремонта: из ремонтов ABAI или, пока ремонт не загружен, из
    статуса «ПРС»/«КРС»."""

    at: datetime
    end_at: datetime | None
    source: Literal["repair", "abai"]
    repair_id: int | None
    # ПРС / КРС / ТРС; None — тип не загружен.
    kind: str | None
    # Вид работ.
    title: str | None


class WellTimelineCaseDTO(BaseModel):
    # failure — отказ (ремонт или простой-отказ); signal — идут эпизоды
    # детекторов, а отказа нет.
    kind: Literal["failure", "signal"]
    is_open: bool
    # Первый по времени привязанный эпизод — узел «Сигнал».
    signal: WellTimelineSignalDTO | None
    # Все привязанные эпизоды по времени, первый совпадает с ``signal``.
    signals: list[WellTimelineSignalDTO]
    breakdown: WellTimelineBreakdownDTO | None
    repair: WellTimelineRepairDTO | None


class WellTimelineDTO(BaseModel):
    well_id: int
    # Сначала идущие случаи, затем от новых к старым.
    cases: list[WellTimelineCaseDTO]
