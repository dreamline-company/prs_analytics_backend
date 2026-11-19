from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.rod_breaks import config
from apps.detectors.rod_breaks.dto.internal.bucket import RawBucket
from apps.telemetry.models.sdmo import SdmoFcData
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository

# Медиана как SQL-агрегат.
_MEDIAN = 0.5


class RodBreakTelemetrySource:
    """Достаёт 2-часовые корзины момента/скорости из app-копии телеметрии.

    Источник — ``telemetry_sdmo_fc_data`` (Postgres, БД ``app``): стабильная
    копия, не sdmo MySQL напрямую. Агрегация (MIN момента, медиана скорости,
    бакетизация по ``savetime``) выполняется в SQL. Границы окна — в явном UTC.
    """

    # Якорь date_bin — фиксированная точка, чтобы границы корзин были
    # детерминированы и одинаковы между прогонами и станциями.
    _BUCKET_ANCHOR = "timestamp '1970-01-01 00:00:00'"

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SdmoFcDataRepository(session)

    def _moment_expr(self) -> ColumnElement:
        # Регистры лежат типизированными колонками r_<addr> (float4).
        return getattr(SdmoFcData, f"r_{config.MOMENT_REGISTER}")

    def _speed_expr(self) -> ColumnElement:
        return getattr(SdmoFcData, f"r_{config.SPEED_REGISTER}")

    def _bucket_expr(self) -> ColumnElement:
        return func.date_bin(
            sa.text(f"interval '{config.BUCKET_HOURS} hours'"),
            SdmoFcData.savetime,
            sa.text(self._BUCKET_ANCHOR),
        )

    async def load_raw_buckets(
        self,
        station_id: int,
        window_start: datetime,
        window_end: datetime,
    ) -> list[RawBucket]:
        """Собрать 2-часовые корзины по станции за окно.

        В каждой корзине: ``MIN`` момента (важно поймать резкий обвал, который
        среднее сглаживает) и медиана скорости. Глючные значения момента
        (Int32-выбросы) отсекаются диапазоном ``MOMENT_CLIP_MIN..MAX`` до ``MIN``.
        """
        moment = self._moment_expr()
        speed = self._speed_expr()
        bucket = self._bucket_expr()

        working = speed > config.FAILURE_SPD_ABS
        query = (
            select(
                bucket.label("start_ts"),
                func.min(moment).label("mom_min"),
                func.percentile_cont(_MEDIAN).within_group(speed).label("spd"),
                func.count().label("sample_count"),
                func.percentile_cont(_MEDIAN).within_group(moment).label("mom_med"),
                func.sum(moment).label("mom_sum"),
                func.sum(moment).filter(working).label("work_sum"),
                func.count().filter(working).label("work_n"),
            )
            .where(
                SdmoFcData.station_id == station_id,
                SdmoFcData.savetime >= window_start,
                SdmoFcData.savetime <= window_end,
                moment >= config.MOMENT_CLIP_MIN,
                moment <= config.MOMENT_CLIP_MAX,
            )
            .group_by(bucket)
            .order_by(bucket)
        )

        rows = await self.repo.fetch_all(query)
        return [
            RawBucket(
                start_ts=row["start_ts"],
                mom_min=float(row["mom_min"]),
                spd=float(row["spd"]) if row["spd"] is not None else 0.0,
                sample_count=int(row["sample_count"]),
                mom_med=float(row["mom_med"]) if row["mom_med"] is not None else None,
                mom_sum=float(row["mom_sum"] or 0.0),
                work_sum=float(row["work_sum"] or 0.0),
                work_n=int(row["work_n"] or 0),
            )
            for row in rows
        ]

    async def get_working_mean(
        self,
        station_id: int,
        window_start: datetime,
        window_end: datetime,
    ) -> tuple[float | None, int]:
        """Средний момент на рабочих отсчётах в ``[window_start, window_end)``.

        База эпизода R2 (сутки до T0), когда корзин того окна в памяти нет —
        у эпизода, открытого старым кодом. Возвращает среднее и число отсчётов.
        """
        moment = self._moment_expr()
        speed = self._speed_expr()
        query = select(func.avg(moment), func.count()).where(
            SdmoFcData.station_id == station_id,
            SdmoFcData.savetime >= window_start,
            SdmoFcData.savetime < window_end,
            moment >= config.MOMENT_CLIP_MIN,
            moment <= config.MOMENT_CLIP_MAX,
            speed > config.FAILURE_SPD_ABS,
        )
        mean, count = (await self.session.execute(query)).one()
        return (float(mean) if mean is not None else None), int(count or 0)

    async def get_base_moment(
        self,
        station_id: int,
        window_start: datetime,
        window_end: datetime,
    ) -> float | None:
        """Базовый момент скважины — медиана момента на работающих отсчётах.

        «Работающий» = скорость выше ``FAILURE_SPD_ABS``. Нужен для порога
        слабых скважин и восстановления ``failure_dt``. ``None`` — если рабочих
        отсчётов в окне нет.
        """
        moment = self._moment_expr()
        speed = self._speed_expr()

        query = select(
            func.percentile_cont(_MEDIAN).within_group(moment),
        ).where(
            SdmoFcData.station_id == station_id,
            SdmoFcData.savetime >= window_start,
            SdmoFcData.savetime <= window_end,
            moment >= config.MOMENT_CLIP_MIN,
            moment <= config.MOMENT_CLIP_MAX,
            speed > config.FAILURE_SPD_ABS,
        )

        result = await self.session.execute(query)
        base = result.scalar_one_or_none()
        return float(base) if base is not None else None
