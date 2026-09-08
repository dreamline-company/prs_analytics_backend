"""Инкрементальная загрузка ГДИС из ABAI: метрики, исследования, значения.

Порядок фиксированный, шаги зависят друг от друга:

1. Справочник метрик — полный upsert (сотни строк).
2. Исследования (``gdis_current``) — keyset по ``id > max(abai_id)``. Строки по
   скважинам, которых нет в ``wells_well``, пропускаются (FK).
3. Значения (``gdis_current_value``) — keyset по ``id``; значения пропущенных
   исследований и неизвестных метрик пропускаются.
4. Перечитывание недавних исследований: в источнике нет отметки изменения, а
   заключения к исследованию дописывают позже, поэтому исследования за
   последние ``REFRESH_DAYS`` и их значения перечитываются целиком и
   обновляются upsert'ом.

Запуск вручную (первичная заливка тем же скриптом):
    cd src && python -m apps.wells.tasks.load_gdis.load_gdis
"""

import asyncio
from collections.abc import Sequence
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.models_registry import *  # noqa: F403
from apps.wells.dto.internal.repositories.gdis import (
    CreateGdisCurrentDTO,
    CreateGdisCurrentValueDTO,
    CreateGdisMetricDTO,
)
from apps.wells.repositories import (
    GdisCurrentRepository,
    GdisCurrentValueRepository,
    GdisMetricRepository,
    WellRepository,
)
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.integrations.abai.models import GdisCurrent as ABAIGdisCurrent
from shared.integrations.abai.models import GdisCurrentValue as ABAIGdisCurrentValue
from shared.integrations.abai.models import Metric as ABAIMetric
from shared.integrations.abai.repositories import (
    ABAIGdisCurrentRepository,
    ABAIGdisCurrentValueRepository,
    ABAIMetricRepository,
)

logger = get_logger(__name__)


def metric_dto(row: ABAIMetric) -> CreateGdisMetricDTO:
    return CreateGdisMetricDTO(
        abai_id=row.id,
        name_ru=row.name_ru,
        name_short_ru=row.name_short_ru,
        code=row.code,
        data_type=row.data_type,
        parent_abai_id=row.parent,
        dict_table=row.dict_table,
        value_double_min=row.value_double_min,
        value_double_max=row.value_double_max,
    )


def current_dto(row: ABAIGdisCurrent) -> CreateGdisCurrentDTO:
    return CreateGdisCurrentDTO(
        abai_id=row.id,
        abai_well_id=row.well,
        meas_date=row.meas_date,
        reason=row.reason,
        reason_txt=row.reason_txt,
        device=row.device,
        target=row.target,
        note=row.note,
        transcript_dynamogram=row.transcript_dynamogram,
        conclusion=row.conclusion,
        conclusion_arr=list(row.conclusion_arr) if row.conclusion_arr else None,
        conclusion_text=row.conclusion_text,
    )


def value_dto(row: ABAIGdisCurrentValue) -> CreateGdisCurrentValueDTO:
    return CreateGdisCurrentValueDTO(
        abai_id=row.id,
        gdis_current_abai_id=row.gdis_curr,
        metric_abai_id=row.metric,
        value_double=row.value_double,
        value_string=row.value_string,
    )


class LoadGdis:
    # Батч keyset-пагинации по источнику.
    ITER_BATCH_SIZE = 5000
    # Размер IN-списка при перечитывании значений недавних исследований.
    REFRESH_CHUNK_SIZE = 500
    # Глубина перечитывания: за столько дней заключения ещё могут дописать.
    REFRESH_DAYS = 90

    def __init__(self, abai_session: AsyncSession, app_session: AsyncSession) -> None:
        self.abai_metric_repo = ABAIMetricRepository(session=abai_session)
        self.abai_current_repo = ABAIGdisCurrentRepository(session=abai_session)
        self.abai_value_repo = ABAIGdisCurrentValueRepository(session=abai_session)
        self.metric_repo = GdisMetricRepository(session=app_session)
        self.current_repo = GdisCurrentRepository(session=app_session)
        self.value_repo = GdisCurrentValueRepository(session=app_session)
        self.well_repo = WellRepository(session=app_session)
        self.app_session = app_session
        self.known_well_ids: set[int] = set()
        self.known_metric_ids: set[int] = set()

    async def run(self, *, on_date: date | None = None) -> None:
        on_date = on_date or date.today()  # noqa: DTZ011
        self.known_well_ids = await self.well_repo.list_abai_ids()
        try:
            metrics = await self._sync_metrics()
            created, skipped = await self._load_new_currents()
            values, values_skipped = await self._load_new_values()
            refreshed, refreshed_values = await self._refresh_recent(
                on_date - timedelta(days=self.REFRESH_DAYS),
            )
        except Exception:
            await self.app_session.rollback()
            logger.exception("Error while loading GDIS")
            raise

        logger.info(
            "GDIS sync finished: metrics=%s, researches created=%s (skipped=%s), "
            "values created=%s (skipped=%s), refreshed researches=%s values=%s",
            metrics,
            created,
            skipped,
            values,
            values_skipped,
            refreshed,
            refreshed_values,
        )

    async def _sync_metrics(self) -> int:
        rows = await self.abai_metric_repo.list_all()
        await self.metric_repo.upsert_many([metric_dto(row) for row in rows])
        await self.app_session.commit()
        self.known_metric_ids = {row.id for row in rows}
        return len(rows)

    async def _load_new_currents(self) -> tuple[int, int]:
        last_abai_id = await self.current_repo.get_max_abai_id()
        created = skipped = 0
        unknown_wells: set[int] = set()
        while True:
            rows = await self.abai_current_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
            )
            if not rows:
                break
            loadable = [row for row in rows if row.well in self.known_well_ids]
            unknown_wells |= {
                row.well for row in rows if row.well not in self.known_well_ids
            }
            await self.current_repo.upsert_many([current_dto(row) for row in loadable])
            await self.app_session.commit()
            created += len(loadable)
            skipped += len(rows) - len(loadable)
            last_abai_id = rows[-1].id
            if len(rows) < self.ITER_BATCH_SIZE:
                break
        if unknown_wells:
            logger.warning(
                "GDIS: %s researches skipped — %s wells unknown to wells_well "
                "(e.g. %s)",
                skipped,
                len(unknown_wells),
                sorted(unknown_wells)[:10],
            )
        return created, skipped

    async def _load_new_values(self) -> tuple[int, int]:
        last_abai_id = await self.value_repo.get_max_abai_id()
        created = skipped = 0
        while True:
            rows = await self.abai_value_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
            )
            if not rows:
                break
            loadable = await self._loadable_values(rows)
            await self.value_repo.upsert_many([value_dto(row) for row in loadable])
            await self.app_session.commit()
            created += len(loadable)
            skipped += len(rows) - len(loadable)
            last_abai_id = rows[-1].id
            if len(rows) < self.ITER_BATCH_SIZE:
                break
        return created, skipped

    async def _loadable_values(
        self,
        rows: Sequence[ABAIGdisCurrentValue],
    ) -> list[ABAIGdisCurrentValue]:
        """Значения только по загруженным исследованиям и известным метрикам."""
        existing = await self.current_repo.list_existing_abai_ids(
            sorted({row.gdis_curr for row in rows}),
        )
        return [
            row
            for row in rows
            if row.gdis_curr in existing and row.metric in self.known_metric_ids
        ]

    async def _refresh_recent(self, since: date) -> tuple[int, int]:
        """Перечитать исследования с ``meas_date >= since`` и их значения."""
        refreshed = refreshed_values = 0
        last_abai_id = 0
        recent_ids: list[int] = []
        while True:
            rows = await self.abai_current_repo.list_after_id(
                last_abai_id,
                limit=self.ITER_BATCH_SIZE,
                since=since,
            )
            if not rows:
                break
            loadable = [row for row in rows if row.well in self.known_well_ids]
            await self.current_repo.upsert_many([current_dto(row) for row in loadable])
            await self.app_session.commit()
            refreshed += len(loadable)
            recent_ids.extend(row.id for row in loadable)
            last_abai_id = rows[-1].id
            if len(rows) < self.ITER_BATCH_SIZE:
                break

        for start in range(0, len(recent_ids), self.REFRESH_CHUNK_SIZE):
            chunk = recent_ids[start : start + self.REFRESH_CHUNK_SIZE]
            values = await self.abai_value_repo.list_by_gdis_ids(chunk)
            loadable = [row for row in values if row.metric in self.known_metric_ids]
            await self.value_repo.upsert_many([value_dto(row) for row in loadable])
            await self.app_session.commit()
            refreshed_values += len(loadable)
        return refreshed, refreshed_values


async def main() -> None:
    async with (
        session_makers["abai"]() as abai_session,
        session_makers["app"]() as app_session,
    ):
        await LoadGdis(abai_session=abai_session, app_session=app_session).run()


@celery_app.task(name="wells.gdis.incremental_load")
def load_gdis_incremental() -> None:
    run_async(main())


if __name__ == "__main__":
    asyncio.run(main())
