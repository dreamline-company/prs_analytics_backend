from datetime import datetime, timedelta

from apps.telemetry.dto.internal.sdmo import SdmoDriveSegmentDTO, SdmoDriveStationDTO
from apps.telemetry.dto.queries.sdmo import ListSdmoDriveSegmentsByWellIdQuery
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository, SdmoStationRepository
from apps.telemetry.services.drive_segments import (
    build_drive_segments,
    last_valid_row,
    vlt_reason_ru,
)
from core.settings import get_settings

# Насколько глубоко искать последний пригодный отсчёт до окна: даже давний, он
# говорит, в каком состоянии станция замолчала (нужно для события после
# разрыва у левого края). Лимит — строк со статусом: мусорные коды идут сериями.
PREVIOUS_ROW_LOOKBACK = timedelta(days=7)
PREVIOUS_ROW_LIMIT = 200


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


def to_local_naive(value: datetime | None) -> datetime | None:
    """``savetime`` СДМО — местное время без пояса; время с поясом приводим к
    нему (иначе asyncpg не сравнит его с колонкой без пояса)."""
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(get_settings().ZONE_INFO).replace(tzinfo=None)


class ListSdmoDriveSegmentsByWellIdUseCase:
    """Отрезки состояния привода по станциям скважины за окно — для пусков и
    остановок на графике (см. ``apps.telemetry.services.drive_segments``)."""

    def __init__(
        self,
        sdmo_station_repository: SdmoStationRepository,
        sdmo_fc_data_repository: SdmoFcDataRepository,
    ) -> None:
        self.sdmo_station_repository = sdmo_station_repository
        self.sdmo_fc_data_repository = sdmo_fc_data_repository

    async def execute(
        self,
        query: ListSdmoDriveSegmentsByWellIdQuery,
        *,
        now: datetime | None = None,
    ) -> list[SdmoDriveStationDTO]:
        stations = await self.sdmo_station_repository.list_by_well_id(
            well_id=query.well_id,
        )
        start_time = to_local_naive(query.start_time)
        end_time = to_local_naive(query.end_time)
        # Правый край для разрыва: конец окна, но не позже «сейчас» — будущее
        # молчанием не считается.
        now = now or local_now()
        window_end = min(end_time, now) if end_time is not None else now

        return [
            await self._station_segments(
                station,
                start_time=start_time,
                end_time=end_time,
                window_end=window_end,
                no_data_gap=timedelta(minutes=query.no_data_gap_minutes),
            )
            for station in sorted(stations, key=lambda station: station.id)
        ]

    async def _station_segments(
        self,
        station: SdmoStation,
        *,
        start_time: datetime | None,
        end_time: datetime | None,
        window_end: datetime,
        no_data_gap: timedelta,
    ) -> SdmoDriveStationDTO:
        rows: list[tuple[datetime, float | None]] = []
        if start_time is not None:
            previous = await self._previous_row(station.id, start_time)
            if previous is not None:
                rows.append(previous)

        period = await self.sdmo_fc_data_repository.list_vlt_status_by_station_period(
            station_id=station.id,
            start_time=start_time,
            end_time=end_time,
        )
        rows.extend((row["savetime"], row["status"]) for row in period)

        segments = build_drive_segments(
            rows,
            no_data_gap=no_data_gap,
            window_start=start_time,
            window_end=window_end,
        )
        return SdmoDriveStationDTO(
            station_id=station.id,
            station_name=station.name,
            type_1900=station.type_1900,
            segments=[
                SdmoDriveSegmentDTO(
                    start_at=segment.start_at,
                    end_at=segment.end_at,
                    state=segment.state.value,
                    code=segment.code,
                    reason_ru=(
                        None
                        if segment.code is None
                        else vlt_reason_ru(segment.code, type_1900=station.type_1900)
                    ),
                    packets=segment.packets,
                )
                for segment in segments
            ],
        )

    async def _previous_row(
        self,
        station_id: int,
        start_time: datetime,
    ) -> tuple[datetime, float | None] | None:
        rows = await self.sdmo_fc_data_repository.list_vlt_status_before(
            station_id,
            before=start_time,
            since=start_time - PREVIOUS_ROW_LOOKBACK,
            limit=PREVIOUS_ROW_LIMIT,
        )
        return last_valid_row((row["savetime"], row["status"]) for row in rows)
