"""Ответы расчёта МРП: по скважине и по методике КМГ."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Verdict = Literal["worse", "better", "watching", "unknown"]


class RepairMrpDTO(BaseModel):
    """МРП вокруг одного ремонта скважины."""

    repair_id: int
    start_time: datetime
    end_time: datetime | None
    repair_type_name_ru: str | None
    is_gtm: bool
    # «По технической причине»: замена устьевого оборудования или штока — ремонт,
    # но не отказ
    is_technical: bool = False
    # Конец предыдущего ремонта → начало этого, сут
    mrp_before_days: float | None
    # Конец этого → начало следующего, сут
    mrp_after_days: float | None
    # Следующего ремонта нет — сколько скважина уже работает, сут
    running_days: float | None
    # worse — МРП после ремонта меньше, чем был перед ним (неэффективный);
    # better — не меньше; watching — следующего ремонта ещё нет, рано судить
    verdict: Verdict
    # Запись склеена с соседней (разрыв меньше суток) — одним ремонтом
    merged_with_adjacent: bool


class WellMrpDTO(BaseModel):
    well_id: int
    well_name: str
    avg_mrp_days: float | None
    intervals_count: int
    # Ремонтов после склейки записей, идущих встык
    repairs_count: int
    # После скольких ремонтов МРП сократился
    worse_count: int
    # Готовая интерпретация (считает код, не модель)
    interpretation: str
    repairs: list[RepairMrpDTO]
