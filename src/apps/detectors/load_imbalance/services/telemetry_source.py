"""Суточные распределения момента из app-копии телеметрии SDMO.

Всё считается одним SQL-запросом на станцию: ворота по скорости, разворот
uint16, дедуп снимков и перцентили. Источник — ``telemetry_sdmo_fc_data``
(Postgres, БД ``app``), стабильная копия, а не MySQL SDMO напрямую.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy import ColumnElement, case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.load_imbalance import config
from apps.detectors.load_imbalance.dto.internal.day import DayAggregate
from apps.telemetry.models.sdmo import SdmoFcData
from apps.telemetry.repositories.sdmo import SdmoFcDataRepository


class LoadImbalanceTelemetrySource:
    """Достаёт суточные P5/P50/P95 момента по станции."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.repo = SdmoFcDataRepository(session)

    @staticmethod
    def _raw_moment() -> ColumnElement:
        return getattr(SdmoFcData, f"r_{config.MOMENT_REGISTER}")

    @staticmethod
    def _speed() -> ColumnElement:
        return getattr(SdmoFcData, f"r_{config.SPEED_REGISTER}")

    @classmethod
    def _moment(cls) -> ColumnElement:
        """Момент со снятым uint16-разворотом.

        Значения выше ``WRAP_LIMIT`` — это отрицательный момент в дополнительном
        коде. Без вычитания периода они уезжают в верх распределения, P5 никогда
        не становится отрицательным, а P95 раздувается — правило слепнет.
        """
        raw = cls._raw_moment()
        return case(
            (raw > config.WRAP_LIMIT, raw - config.WRAP_PERIOD),
            else_=raw,
        )

    async def load_daily(
        self,
        station_sdmo_id: int,
        day_from: date,
        day_to: date,
    ) -> list[DayAggregate]:
        """Суточные агрегаты станции за ``[day_from, day_to]`` включительно.

        В выборку попадают только снимки при работающем приводе: момент при
        стоящем станке болтается около нуля и, если его не выкинуть, притягивает
        P95 вниз и P5 к нулю — K начинает мерить простои, а не насос.

        Дубли снимков (одно и то же ``savetime`` двумя строками источника —
        повторная передача пакета) схлопываются: перцентиль считается по набору
        значений, и дубль получил бы двойной вес, а заодно завысил бы счётчик
        валидности суток.
        """
        raw_moment = self._raw_moment()
        speed = self._speed()

        snapshots = (
            select(SdmoFcData.day.label("day"), self._moment().label("moment"))
            .distinct(SdmoFcData.savetime)
            .where(
                SdmoFcData.sdmo_station_id == station_sdmo_id,
                SdmoFcData.savetime >= datetime.combine(day_from, time.min),
                SdmoFcData.savetime
                < datetime.combine(day_to + timedelta(days=1), time.min),
                raw_moment.is_not(None),
                speed > 0,
            )
            # DISTINCT ON требует ведущего savetime в ORDER BY; sdmo_id —
            # детерминированный tiebreak (побеждает строка источника с меньшим id).
            .order_by(SdmoFcData.savetime, SdmoFcData.sdmo_id)
            .subquery()
        )

        query = (
            select(
                snapshots.c.day.label("day"),
                func.count().label("n_samples"),
                func.percentile_cont(config.P_REVERSE)
                .within_group(snapshots.c.moment)
                .label("p5"),
                func.percentile_cont(config.P_MEDIAN)
                .within_group(snapshots.c.moment)
                .label("p50"),
                func.percentile_cont(config.P_WORKING)
                .within_group(snapshots.c.moment)
                .label("p95"),
            )
            .group_by(snapshots.c.day)
            .order_by(snapshots.c.day)
        )

        rows = await self.repo.fetch_all(query)
        return [
            DayAggregate(
                day=row["day"],
                n_samples=int(row["n_samples"]),
                p5=float(row["p5"]),
                p50=float(row["p50"]),
                p95=float(row["p95"]),
            )
            for row in rows
        ]

    async def get_data_front(self, station_sdmo_id: int) -> date | None:
        """Последние сутки, по которым у станции вообще есть строки.

        Нужны, чтобы не оценивать неполные сутки: если загрузчик отстал,
        сегодняшние (и даже вчерашние) сутки в копии могут быть половинчатыми,
        а порога по числу снимков для защиты мало — 100 снимков набегает
        за несколько часов.
        """
        result = await self.session.execute(
            select(func.max(SdmoFcData.savetime)).where(
                SdmoFcData.sdmo_station_id == station_sdmo_id,
            ),
        )
        last = result.scalar_one_or_none()
        return last.date() if last is not None else None
