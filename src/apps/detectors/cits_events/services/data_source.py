"""Вход правила R10 из наших копий источников.

- замеры ЦИТС — ``telemetry_well`` (время замера местное, как в источнике);
- техрежим — ``telemetry_tech_regime``;
- статусы ABAI — ``wells_well_status`` (UTC; переводятся в местные сутки,
  как у автора ``dbeg + 5 часов``);
- признак работы СУ — регистр 1998 в ``telemetry_sdmo_fc_data``, только по
  тем суткам и скважинам, которые запросило правило.

Замер суток — медиана непустых Qv; сутки, где все замеры пустые, остаются
пустыми (событие 4). Qm_liq мы не зеркалим: в ЦИТС ЖМГ Qv и Qm пусты
одновременно, отдельного запасного канала нет.
"""

import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.cits_events import config
from apps.detectors.cits_events.rule import (
    Measurement,
    RegimeInterval,
    StatusInterval,
    SuDay,
    WellInput,
)
from apps.telemetry.models.sdmo import SdmoFcData, SdmoStation
from apps.telemetry.models.tech_regime import TechRegime
from apps.telemetry.models.telemetry import Telemetry
from apps.wells.models.well import Well
from apps.wells.repositories import WellStatusRepository
from core.settings import get_settings

settings = get_settings()

# Открытый интервал в ABAI — dend 3333-12-31; всё позже этого года — «не закрыт».
OPEN_END_YEAR = 3000


@dataclass(frozen=True, slots=True)
class WellRecord:
    well: WellInput
    well_id: int
    abai_id: int


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def _local_midnight_utc(day: date) -> datetime:
    """Местная полночь суток в наивном UTC — граница для статусов ABAI."""
    local = datetime.combine(day, time.min, tzinfo=settings.ZONE_INFO)
    return local.astimezone(UTC).replace(tzinfo=None)


def _local_day(moment: datetime) -> date:
    return moment.replace(tzinfo=UTC).astimezone(settings.ZONE_INFO).date()


class CitsEventsDataSource:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.status_repo = WellStatusRepository(session)

    async def load_wells(self, fix: date, abai_ngdu_id: int) -> list[WellRecord]:
        """Скважины НГДУ с замерами в окне правила и всё, что им нужно."""
        dfrom = fix - timedelta(days=config.WINDOW_D)
        until = fix + timedelta(days=1)

        rows = await self.session.execute(
            select(
                Well.id,
                Well.name,
                Well.abai_id,
                Telemetry.date_time,
                Telemetry.qv_liquid,
            )
            .join(Well, Well.id == Telemetry.well_id)
            .where(
                Telemetry.abai_ngdu_id == abai_ngdu_id,
                Telemetry.date_time >= _day_start(dfrom),
                Telemetry.date_time < _day_start(until),
            ),
        )
        wells: dict[int, tuple[str, int]] = {}
        by_day: dict[int, dict[date, list[float | None]]] = defaultdict(
            lambda: defaultdict(list),
        )
        for well_id, name, abai_id, date_time, qv in rows.all():
            wells[well_id] = (name, abai_id)
            by_day[well_id][date_time.date()].append(qv)
        if not wells:
            return []

        abai_ids = [abai_id for _, abai_id in wells.values()]
        regimes = await self._regimes(abai_ids, dfrom=dfrom, until=until)
        statuses = await self._statuses(abai_ids, dfrom=dfrom, until=until)

        records = []
        for well_id, (name, abai_id) in sorted(wells.items(), key=lambda x: x[1][0]):
            series = []
            for day in sorted(by_day[well_id]):
                values = [q for q in by_day[well_id][day] if q is not None]
                series.append(
                    Measurement(day, statistics.median(values) if values else None),
                )
            records.append(
                WellRecord(
                    well=WellInput(
                        name=name,
                        series=series,
                        regimes=regimes.get(abai_id, []),
                        statuses=statuses.get(abai_id, []),
                    ),
                    well_id=well_id,
                    abai_id=abai_id,
                ),
            )
        return records

    async def _regimes(
        self,
        abai_ids: list[int],
        *,
        dfrom: date,
        until: date,
    ) -> dict[int, list[RegimeInterval]]:
        # Интервалы, закончившиеся раньше самой старой допустимой даты
        # техрежима, не нужны: старше TM_MAX_AGE_D он всё равно не годится.
        tm_from = dfrom - timedelta(days=config.TM_MAX_AGE_D)
        rows = await self.session.execute(
            select(TechRegime.abai_well_id, TechRegime.start_date, TechRegime.liquid)
            .where(
                TechRegime.abai_well_id.in_(abai_ids),
                TechRegime.start_date < until,
                TechRegime.end_date >= tm_from,
            )
            .order_by(
                TechRegime.abai_well_id,
                TechRegime.start_date,
                TechRegime.abai_id,
            ),
        )
        result: dict[int, list[RegimeInterval]] = defaultdict(list)
        for abai_well_id, start, liquid in rows.all():
            result[abai_well_id].append(RegimeInterval(start, liquid))
        return result

    async def _statuses(
        self,
        abai_ids: list[int],
        *,
        dfrom: date,
        until: date,
    ) -> dict[int, list[StatusInterval]]:
        rows = await self.status_repo.list_intervals_by_abai_well_ids(
            abai_ids,
            since=_local_midnight_utc(dfrom),
            until=_local_midnight_utc(until),
        )
        result: dict[int, list[StatusInterval]] = defaultdict(list)
        for abai_well_id, dbeg, dend, code, name, reason in rows:
            result[abai_well_id].append(
                StatusInterval(
                    start=_local_day(dbeg),
                    end=None if dend.year > OPEN_END_YEAR else _local_day(dend),
                    code=code,
                    name=name,
                    reason=reason,
                ),
            )
        return result

    async def load_su(
        self,
        requests: dict[str, set[date]],
        abai_ngdu_id: int,
    ) -> dict[str, dict[date, SuDay]]:
        """Записи регистра 1998 по запрошенным суткам скважин.

        Станция ищется по коду (с синонимами ZBN/ZBR), как у автора: у станций
        ZBR в СДМО нет привязки к скважине. Несколько активных станций на
        скважину суммируются.
        """
        if not requests:
            return {}

        code_to_well: dict[str, str] = {}
        for name in requests:
            prefix, _, number = name.partition("_")
            for alias in config.STATION_CODE_ALIASES.get(prefix, (prefix,)):
                code_to_well[f"{alias}_{number}"] = name
        stations = await self.session.execute(
            select(SdmoStation.id, SdmoStation.code).where(
                SdmoStation.abai_ngdu_id == abai_ngdu_id,
                SdmoStation.active.is_(True),
                SdmoStation.code.in_(list(code_to_well)),
            ),
        )
        station_ids: dict[str, list[int]] = defaultdict(list)
        for station_id, code in stations.all():
            station_ids[code_to_well[code]].append(station_id)

        speed = getattr(SdmoFcData, f"r_{config.SPEED_REGISTER}")
        result: dict[str, dict[date, SuDay]] = {}
        for name, days in requests.items():
            ids = station_ids.get(name)
            if not ids:
                continue
            first, last = min(days), max(days)
            rows = await self.session.execute(
                select(
                    SdmoFcData.day,
                    func.count(speed),
                    func.sum(case((speed > 0, 1), else_=0)),
                )
                .where(
                    SdmoFcData.station_id.in_(ids),
                    # Индекс — по (station_id, savetime); day им не покрыт.
                    SdmoFcData.savetime >= _day_start(first - timedelta(days=1)),
                    SdmoFcData.savetime < _day_start(last + timedelta(days=2)),
                    SdmoFcData.day.between(first, last),
                )
                .group_by(SdmoFcData.day),
            )
            result[name] = {
                day: SuDay(int(n), int(n_run or 0))
                for day, n, n_run in rows.all()
                if day in days
            }
        return result
