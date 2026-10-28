"""Показатели из ГДИС для паспорта скважины и ИИ-разбора динамограмм.

Источник — зеркало ``wells_gdis_*``. Ндин выбирается по имени в справочнике
``metric``, как в отчётных запросах ABAI: динамический уровень заведён под
двумя именами. Параметры динамограммы — по коду метрики.
"""

from collections.abc import Sequence
from datetime import date
from typing import Final

from pydantic import BaseModel

from apps.wells.dto.internal.dynamogram import DynamogramGdisDTO
from apps.wells.repositories.gdis import (
    GdisCurrentRepository,
    GdisCurrentValueRepository,
    GdisMetricRepository,
)

# emg.metric.name_ru для динамического уровня (Ндин), м.
DYNAMIC_LEVEL_METRIC_NAMES: Final[tuple[str, ...]] = (
    "H дин, м",
    "Динамический уровень, м",
)

# emg.metric.code → поле DynamogramGdisDTO: параметры исследования, которые
# получает модель при разборе динамограммы.
DYNAMOGRAM_METRIC_FIELDS: Final[dict[str, str]] = {
    "PLST": "stroke_length_m",  # Длина хода, м
    "PMPR": "strokes_per_min",  # Число качаний
    "MLP": "load_max",  # P максимальной нагрузки
    "RMNZ": "load_min",  # P минимальной нагрузки
    "ADMCF": "fill_coef",  # K наполнения, д.ед.
    "PDCF": "pump_feed_coef",  # K подачи насоса, д.ед.
}


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


class DynamogramGdisService:
    """Параметры ГДИС на день динамограммы.

    Файл динамограммы в ABAI привязан к дате замера без времени, исследование
    ГДИС — тоже; связь — (скважина, дата). За день бывает несколько
    исследований: значение каждой метрики берётся из самого позднего, где оно
    есть, заключение оператора — тоже. Ноль и отрицательные значения — в ABAI
    это заглушка «не измерено»: они не перекрывают замер из другого
    исследования того же дня и в промпт не идут.
    """

    def __init__(
        self,
        *,
        gdis_metric_repository: GdisMetricRepository,
        gdis_current_repository: GdisCurrentRepository,
        gdis_current_value_repository: GdisCurrentValueRepository,
    ) -> None:
        self.gdis_metric_repository = gdis_metric_repository
        self.gdis_current_repository = gdis_current_repository
        self.gdis_current_value_repository = gdis_current_value_repository
        self._field_by_metric_id: dict[int, str] | None = None

    async def get_for_dynamogram(
        self,
        abai_well_id: int,
        meas_date: date,
    ) -> DynamogramGdisDTO | None:
        """``None`` — исследования за этот день нет."""
        studies = await self.gdis_current_repository.list_by_abai_well_id_and_date(
            abai_well_id,
            meas_date,
        )
        if not studies:
            return None
        field_by_metric_id = await self._metric_fields()
        values = await self.gdis_current_value_repository.list_by_gdis_abai_ids(
            [study.abai_id for study in studies],
        )
        study_rank = {study.abai_id: rank for rank, study in enumerate(studies)}
        fields: dict[str, float] = {}
        for value in sorted(
            values,
            key=lambda v: (study_rank[v.gdis_current_abai_id], -v.abai_id),
        ):
            field = field_by_metric_id.get(value.metric_abai_id)
            if (
                field is None
                or value.value_double is None
                or value.value_double <= 0
                or field in fields
            ):
                continue
            fields[field] = value.value_double
        conclusion = next(
            (
                study.conclusion_text.strip()
                for study in studies
                if study.conclusion_text and study.conclusion_text.strip()
            ),
            None,
        )
        return DynamogramGdisDTO(
            meas_date=meas_date,
            operator_conclusion=conclusion,
            **fields,
        )

    async def _metric_fields(self) -> dict[int, str]:
        if self._field_by_metric_id is None:
            ids_by_code = await self.gdis_metric_repository.map_abai_ids_by_codes(
                list(DYNAMOGRAM_METRIC_FIELDS),
            )
            self._field_by_metric_id = {
                metric_id: DYNAMOGRAM_METRIC_FIELDS[code]
                for code, metric_id in ids_by_code.items()
            }
        return self._field_by_metric_id
