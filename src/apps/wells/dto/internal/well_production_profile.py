from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel


class ProfilePeriod(StrEnum):
    """Сутки и 3 дня — точки по часу, неделя и месяц (с 1-го числа) — по суткам."""

    DAY = "day"
    THREE_DAYS = "3d"
    WEEK = "week"
    MONTH = "month"


class ProductionProfilePointDTO(BaseModel):
    """Точка графика — час (сутки, 3 дня) или сутки (неделя, месяц).

    ``fact_oil`` = ``oil_rate`` × ``work_share``; простой всю точку — 0;
    None — разрыв: нет дебита или доли работы, либо в периоде нет замеров.
    """

    time: datetime  # начало часа или суток, местное время
    plan_oil: float | None
    fact_oil: float | None
    oil_rate: float | None  # медиана трёх последних замеров ЦИТС, т/сут
    work_share: float | None  # 0…1


class ProductionProfileMeasurementDTO(BaseModel):
    time: datetime
    oil: float | None  # т/сут
    liquid: float | None  # м³/сут


class WellProductionProfileDTO(BaseModel):
    """Профиль добычи за период (WellProductionProfileService).

    Итоги — средние по точкам с весом длительности (добыча ÷ длительность).
    Для экрана: ``fact_oil`` None — «Замера нет» (в периоде нет замеров в
    работе либо доли работы не узнать — ни СДМО, ни статусов ABAI). Простой
    весь период — ``fact_oil`` 0 при ``has_measurements`` False.
    """

    well_id: int
    well_name: str
    period: ProfilePeriod
    step: Literal["hour", "day"]
    period_from: datetime
    period_to: datetime
    plan_oil: float | None
    fact_oil: float | None
    oil_rate_working: float | None  # средний дебит, пока скважина работала
    has_measurements: bool
    work_share: float | None
    work_share_source: str | None  # sdmo / abai / mixed
    measurements_count: int
    points: list[ProductionProfilePointDTO]
    measurements: list[ProductionProfileMeasurementDTO]
