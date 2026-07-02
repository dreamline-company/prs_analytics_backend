"""Builds a repair chronology from the analytics data.

Fixed 9-event order per product spec:

  1. ПОР                 — File.created_at of RepairDoc.por_file_id
  2. Динамограмма до     — Dynamogram(before).snapshot_time
  3. Начало СПО          — earliest SPO.snapshot_time in the repair window
  4. Спецтехника         — placeholder (source not wired)
  5. Сводка              — earliest RepairSummary.date for the repair
  6. Конец СПО           — latest SPO.snapshot_time in the repair window
  7. Запуск              — Repair.end_time
  8. Выход на тех. режим — Repair.end_time (same as launch for now)
  9. Акт ПРС             — File.created_at of RepairDoc.act_file_id
"""

import datetime

from apps.files.repositories.file import FileRepository
from apps.repairs.dto.internal.timeline import (
    RepairTimelineDTO,
    RepairTimelineEventDTO,
)
from apps.repairs.dto.queries.timeline import GetRepairTimelineQuery
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
)
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from shared.errors import HttpError
from starlette import status


class RepairNotFoundError(HttpError):
    message = "Repair not found."
    code = "repair_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetRepairTimelineUseCase:
    def __init__(  # noqa: PLR0913
        self,
        *,
        repair_repository: RepairRepository,
        analytics_repository: RepairAnalyticsRepository,
        analytics_dynamogram_repository: RepairAnalyticsDynamogramRepository,
        dynamogram_repository: DynamogramRepository,
        spo_repository: SPORepository,
        doc_repository: RepairDocRepository,
        summary_repository: RepairSummaryRepository,
        file_repository: FileRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.analytics_repository = analytics_repository
        self.analytics_dynamogram_repository = analytics_dynamogram_repository
        self.dynamogram_repository = dynamogram_repository
        self.spo_repository = spo_repository
        self.doc_repository = doc_repository
        self.summary_repository = summary_repository
        self.file_repository = file_repository

    async def execute(
        self,
        query: GetRepairTimelineQuery,
    ) -> RepairTimelineDTO:
        repair = await self.repair_repository.get_by_id(query.repair_id)
        if repair is None:
            raise RepairNotFoundError(details={"repair_id": query.repair_id})

        analytics = await self.analytics_repository.get_by_repair_id(query.repair_id)

        por_date, act_date = await self._por_act_dates(query.repair_id)
        dyn_before_date = await self._dynamogram_before_date(
            analytics.id if analytics is not None else None,
        )
        spo_start, spo_end = await self._spo_window_dates(
            well_id=repair.well_id,
            start_time=repair.start_time,
            end_time=repair.end_time,
        )
        summary_date = await self._first_summary_date(query.repair_id)
        end_date = (
            repair.end_time.date() if repair.end_time is not None else None
        )

        events = [
            RepairTimelineEventDTO(code="por", label="ПОР", date=por_date),
            RepairTimelineEventDTO(
                code="dynamogram_before",
                label="Динамограмма до",
                date=dyn_before_date,
            ),
            RepairTimelineEventDTO(
                code="spo_start",
                label="Начало СПО",
                date=spo_start,
            ),
            RepairTimelineEventDTO(
                code="special_equipment",
                label="Спецтехника",
                date=None,
            ),
            RepairTimelineEventDTO(
                code="summary",
                label="Сводка",
                date=summary_date,
            ),
            RepairTimelineEventDTO(code="spo_end", label="Конец СПО", date=spo_end),
            RepairTimelineEventDTO(code="launch", label="Запуск", date=end_date),
            RepairTimelineEventDTO(
                code="tech_mode",
                label="Выход на тех. режим",
                date=end_date,
            ),
            RepairTimelineEventDTO(code="act", label="Акт ПРС", date=act_date),
        ]

        return RepairTimelineDTO(repair_id=repair.id, events=events)

    async def _por_act_dates(
        self,
        repair_id: int,
    ) -> tuple[datetime.date | None, datetime.date | None]:
        doc = await self.doc_repository.get_by_repair_id(repair_id)
        if doc is None:
            return None, None

        por_date = (
            await self._file_created_date(doc.por_file_id)
            if doc.por_file_id is not None
            else None
        )
        act_date = (
            await self._file_created_date(doc.act_file_id)
            if doc.act_file_id is not None
            else None
        )
        return por_date, act_date

    async def _file_created_date(self, file_id: int) -> datetime.date | None:
        file_row = await self.file_repository.get_by_id(file_id)
        return file_row.created_at.date() if file_row is not None else None

    async def _dynamogram_before_date(
        self,
        analytics_id: int | None,
    ) -> datetime.date | None:
        if analytics_id is None:
            return None
        link = await self.analytics_dynamogram_repository.get_by_analytics_id(
            analytics_id,
        )
        if link is None or link.dynamogram_before_id is None:
            return None
        dynamograms = await self.dynamogram_repository.list_by_ids(
            [link.dynamogram_before_id],
        )
        if not dynamograms:
            return None
        return dynamograms[0].snapshot_time.date()

    async def _spo_window_dates(
        self,
        *,
        well_id: int | None,
        start_time: datetime.datetime,
        end_time: datetime.datetime | None,
    ) -> tuple[datetime.date | None, datetime.date | None]:
        if well_id is None:
            return None, None
        spos = await self.spo_repository.list_by_well_id_in_window(
            well_id=well_id,
            start=start_time,
            end=end_time,
        )
        if not spos:
            return None, None
        # list_by_well_id_in_window returns ascending by snapshot_time.
        return spos[0].snapshot_time.date(), spos[-1].snapshot_time.date()

    async def _first_summary_date(
        self,
        repair_id: int,
    ) -> datetime.date | None:
        summaries = await self.summary_repository.list_by_repair_id(repair_id)
        return summaries[0].date if summaries else None
