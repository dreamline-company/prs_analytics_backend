"""Показатели из ГДИС для паспорта скважины.

Источник — зеркало ``wells_gdis_*``. Метрика выбирается по имени в справочнике
``metric``, как в отчётных запросах ABAI: динамический уровень заведён под
двумя именами.
"""

from collections.abc import Sequence
from datetime import date
from typing import Final

from pydantic import BaseModel

from apps.wells.repositories.gdis import (
    GdisCurrentValueRepository,
    GdisMetricRepository,
)

# emg.metric.name_ru для динамического уровня (Ндин), м.
DYNAMIC_LEVEL_METRIC_NAMES: Final[tuple[str, ...]] = (
    "H дин, м",
    "Динамический уровень, м",
)


class DynamicLevelDTO(BaseModel):
    """Последний замер Ндин: значение и дата исследования."""

    h_din_m: float
    meas_date: date


class WellGdisService:
    def __init__(
        self,
        *,
        gdis_metric_repository: GdisMetricRepository,
        gdis_current_value_repository: GdisCurrentValueRepository,
    ) -> None:
        self.gdis_metric_repository = gdis_metric_repository
        self.gdis_current_value_repository = gdis_current_value_repository

    async def get_last_dynamic_level(
        self,
        abai_well_id: int,
    ) -> DynamicLevelDTO | None:
        levels = await self.get_last_dynamic_level_by_abai_well_ids([abai_well_id])
        return levels.get(abai_well_id)

    async def get_last_dynamic_level_by_abai_well_ids(
        self,
        abai_well_ids: Sequence[int],
    ) -> dict[int, DynamicLevelDTO]:
        """Ключ — ``abai_well_id``; скважины без замера Ндин в ответ не попадают.

        Берётся последнее по ``meas_date`` исследование, где есть непустое
        значение метрики: исследование без Ндин не обнуляет показатель, а
        пропускается в пользу более раннего.
        """
        metric_ids = await self.gdis_metric_repository.list_abai_ids_by_names(
            DYNAMIC_LEVEL_METRIC_NAMES,
        )
        if not metric_ids:
            return {}
        rows = await self.gdis_current_value_repository.get_last_by_abai_well_ids(
            abai_well_ids,
            metric_abai_ids=sorted(metric_ids),
        )
        return {
            abai_well_id: DynamicLevelDTO(h_din_m=value, meas_date=meas_date)
            for abai_well_id, value, meas_date in rows
        }
