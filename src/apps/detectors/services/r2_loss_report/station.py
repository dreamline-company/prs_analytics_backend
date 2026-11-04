"""Станция СДМО эпизода: норма момента и что было с приводом после сигнала.

``telemetry_sdmo_fc_data`` — сотни миллионов строк, индекс только
``(station_id, savetime)``. Поэтому сначала короткие запросы, решающие, попадёт
ли скважина в отчёт (норма за сутки до сигнала, последние часы дня), а начало
цепочки и подробности для текста — только по не добывающим.
"""

import math
import statistics
from datetime import datetime, timedelta

from sqlalchemy import ColumnElement, and_, func, not_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.services.r2_loss_report import config
from apps.detectors.services.r2_loss_report.rows import DriveFacts
from apps.telemetry.models.sdmo import SdmoFcData
from apps.telemetry.services.drive import (
    DRIVE_RUNNING_VLT_CODES,
    DRIVE_STOPPED_VLT_CODES,
    drive_running,
)

_KNOWN_CODES = sorted(DRIVE_RUNNING_VLT_CODES | DRIVE_STOPPED_VLT_CODES)


def _moment_ok() -> ColumnElement[bool]:
    return and_(
        SdmoFcData.r_1991 >= config.MOMENT_CLIP_MIN,
        SdmoFcData.r_1991 <= config.MOMENT_CLIP_MAX,
    )


def _stopped() -> ColumnElement[bool]:
    """Привод стоит — как :func:`drive_running`: код остановки, а без кода
    (или с неизвестным) — нулевые обороты."""
    code = func.round(SdmoFcData.r_1999)
    return or_(
        code.in_(sorted(DRIVE_STOPPED_VLT_CODES)),
        and_(
            or_(SdmoFcData.r_1999.is_(None), not_(code.in_(_KNOWN_CODES))),
            SdmoFcData.r_1998 == 0,
        ),
    )


def _finite(value: float | None) -> float | None:
    if value is None or not math.isfinite(value):
        return None
    return value


class StationReader:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def base_moment(
        self,
        station_id: int,
        signal_at: datetime,
        *,
        window: timedelta,
    ) -> float | None:
        """Медиана момента при вращении за ``window`` до сигнала."""
        value = await self.session.scalar(
            select(func.percentile_cont(0.5).within_group(SdmoFcData.r_1991)).where(
                SdmoFcData.station_id == station_id,
                SdmoFcData.savetime >= signal_at - window,
                SdmoFcData.savetime < signal_at,
                SdmoFcData.r_1998 > config.BASE_MIN_RPM,
                _moment_ok(),
            ),
        )
        return _finite(float(value)) if value is not None else None

    async def end_state(
        self,
        station_id: int,
        since: datetime,
        until: datetime,
    ) -> tuple[int, int, float | None]:
        """(отсчётов, из них с вращением, медиана момента при вращении)."""
        if since >= until:
            return 0, 0, None
        running = SdmoFcData.r_1998 > config.BASE_MIN_RPM
        total, spinning, moment = (
            await self.session.execute(
                select(
                    func.count(),
                    func.count().filter(running),
                    func.percentile_cont(0.5)
                    .within_group(SdmoFcData.r_1991)
                    .filter(running, _moment_ok()),
                ).where(
                    SdmoFcData.station_id == station_id,
                    SdmoFcData.savetime >= since,
                    SdmoFcData.savetime < until,
                ),
            )
        ).one()
        return (
            int(total or 0),
            int(spinning or 0),
            _finite(float(moment)) if moment is not None else None,
        )

    async def worked(
        self,
        station_id: int,
        since: datetime,
        until: datetime,
        *,
        base: float,
    ) -> bool:
        """Была ли в [since, until) устойчивая работа: ``CHAIN_WORK_SAMPLES``
        отсчётов вращения с моментом у нормы (поиск обрывается на нужном числе)."""
        if since >= until:
            return False
        normal = (
            select(SdmoFcData.savetime)
            .where(
                SdmoFcData.station_id == station_id,
                SdmoFcData.savetime >= since,
                SdmoFcData.savetime < until,
                SdmoFcData.r_1998 > config.BASE_MIN_RPM,
                SdmoFcData.r_1991 >= config.PRODUCING_MOMENT_SHARE * base,
                SdmoFcData.r_1991 <= config.MOMENT_CLIP_MAX,
            )
            .order_by(SdmoFcData.savetime.desc())
            .limit(config.CHAIN_WORK_SAMPLES)
            .subquery()
        )
        count = await self.session.scalar(select(func.count()).select_from(normal))
        return int(count or 0) >= config.CHAIN_WORK_SAMPLES

    async def details(
        self,
        station_id: int,
        signal_at: datetime,
        *,
        day_end: datetime,
        drive: DriveFacts,
    ) -> DriveFacts:
        """Факты для текста «Что происходит» и простоя."""
        window_end = min(
            signal_at + timedelta(minutes=config.DRIVE_AFTER_MINUTES),
            day_end,
        )
        after = (
            await self.session.execute(
                select(SdmoFcData.r_1991, SdmoFcData.r_1998, SdmoFcData.r_1999).where(
                    SdmoFcData.station_id == station_id,
                    SdmoFcData.savetime >= signal_at,
                    SdmoFcData.savetime < window_end,
                ),
            )
        ).all()
        running = [row for row in after if drive_running(row[2], row[1])]
        moments = [
            row[0]
            for row in running
            if (value := _finite(row[0])) is not None
            and config.MOMENT_CLIP_MIN <= value <= config.MOMENT_CLIP_MAX
        ]
        speeds = [row[1] for row in running if _finite(row[1]) is not None]

        stopped_at = await self.session.scalar(
            select(SdmoFcData.savetime)
            .where(
                SdmoFcData.station_id == station_id,
                SdmoFcData.savetime >= signal_at,
                SdmoFcData.savetime < day_end,
                _stopped(),
            )
            .order_by(SdmoFcData.savetime.asc())
            .limit(1),
        )
        last = (
            await self.session.execute(
                select(SdmoFcData.r_1998, SdmoFcData.r_1999)
                .where(
                    SdmoFcData.station_id == station_id,
                    SdmoFcData.savetime < day_end,
                )
                .order_by(SdmoFcData.savetime.desc())
                .limit(1),
            )
        ).first()
        running_at_end = drive_running(last[1], last[0]) if last else None
        last_running_at = None
        if running_at_end is False:
            last_running_at = await self.session.scalar(
                select(SdmoFcData.savetime)
                .where(
                    SdmoFcData.station_id == station_id,
                    SdmoFcData.savetime >= signal_at,
                    SdmoFcData.savetime < day_end,
                    not_(_stopped()),
                    SdmoFcData.r_1998 > 0,
                )
                .order_by(SdmoFcData.savetime.desc())
                .limit(1),
            )
        return DriveFacts(
            base_moment=drive.base_moment,
            reference_moment=drive.reference_moment,
            has_end_data=drive.has_end_data,
            end_running=drive.end_running,
            moment_end=drive.moment_end,
            moment_after=statistics.median(moments) if moments else None,
            rpm_after=statistics.median(speeds) if speeds else None,
            stopped_at=stopped_at,
            running_at_end=running_at_end,
            last_running_at=last_running_at,
        )
