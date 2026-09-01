"""Курсорный прогон R2: инкрементальная детекция эпизодов обрыва штанги.

Запускается диспетчером после прихода новых данных SDMO (и подметальщиком по
отставшим курсорам). По каждой целевой станции: курсор -> телеметрия с
24ч-контекстом -> state machine эпизода -> upsert инцидентов -> сдвиг курсора.
Инциденты и курсор пишутся в одной транзакции; повторная обработка того же
окна идемпотентна (частичный уникальный индекс инцидентов).
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.conclusion.notify import notify_conclusion
from apps.detectors.dto.internal.repositories.incident import OpenIncidentDTO
from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_NORMALIZED,
)
from apps.detectors.repositories import (
    DetectorCursorRepository,
    DetectorIncidentRepository,
)
from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.dto.internal.bucket import Bucket2h
from apps.detectors.rod_breaks.services import bucketizer, failure_dt
from apps.detectors.rod_breaks.services.episode import (
    ACTION_CONFIRM,
    ACTION_ESCALATE,
    ACTION_NORMALIZE,
    ACTION_OPEN_ALARM,
    ACTION_OPEN_WARNING,
    EpisodeAction,
    evaluate_episode,
)
from apps.detectors.rod_breaks.services.telemetry_source import (
    RodBreakTelemetrySource,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import SdmoStationRepository
from core import get_logger
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)

DETECTOR_CODE = "R2"
# Целевой флот детектора — Danfoss VLT (ЭВН-КУДУ).
TARGET_TYPE_1900 = 6

_BUCKET_SPAN = timedelta(hours=config.BUCKET_HOURS)
_CONTEXT = timedelta(hours=config.MEDIAN_WINDOW_HOURS)


async def target_stations(
    session: AsyncSession,
    entity_ids: list[int] | None = None,
) -> list[SdmoStation]:
    """Целевые станции: type_1900=6 с привязкой к скважине, одна на скважину.

    У скважины бывает несколько станций — берётся станция с минимальным
    sdmo_id, чтобы эпизоды скважины всегда считались по одной ленте.
    """
    filters = [
        SdmoStation.type_1900 == TARGET_TYPE_1900,
        SdmoStation.well_id.is_not(None),
    ]
    if entity_ids:
        filters.append(SdmoStation.sdmo_id.in_(entity_ids))

    stations = await SdmoStationRepository(session).get_list(
        QuerySpec(filters=tuple(filters), order_by=(SdmoStation.sdmo_id,)),
    )
    by_well: dict[int, SdmoStation] = {}
    for station in stations:
        by_well.setdefault(station.well_id, station)
    return sorted(by_well.values(), key=lambda s: s.sdmo_id)


class RodBreakIncidentRunner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.source = RodBreakTelemetrySource(session)
        self.incident_repo = DetectorIncidentRepository(session)
        self.cursor_repo = DetectorCursorRepository(session)

    async def run(self, entity_ids: list[int] | None = None) -> None:
        stations = await target_stations(self.session, entity_ids)
        cursors = await self.cursor_repo.get_map(
            DETECTOR_CODE,
            [s.sdmo_id for s in stations] or None,
        )

        opened = escalated = normalized = 0
        for station in stations:
            cursor = cursors.get(station.sdmo_id)
            try:
                o, e, n = await self._run_station(
                    station,
                    cursor_ts=cursor.last_event_at if cursor else None,
                )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception(
                    "R2 station %s failed; cursor kept",
                    station.sdmo_id,
                )
                continue
            opened += o
            escalated += e
            normalized += n
            # Открытие/эскалация меняют уровень эпизода — будим ИИ-заключение.
            if o or e:
                await self._notify_conclusion(station.well_id)

        if opened or escalated or normalized:
            logger.info(
                "R2 incidents: opened=%s, escalated=%s, normalized=%s (stations=%s)",
                opened,
                escalated,
                normalized,
                len(stations),
            )

    async def _notify_conclusion(self, well_id: int) -> None:
        active = await self.incident_repo.get_active(
            detector_code=DETECTOR_CODE,
            well_id=well_id,
            reason_code=incident_config.REASON_ROD_BREAK,
        )
        if active is not None:
            notify_conclusion(active.id)

    async def _run_station(
        self,
        station: SdmoStation,
        *,
        cursor_ts: datetime | None,
    ) -> tuple[int, int, int]:
        now = datetime.now(UTC).replace(tzinfo=None)
        # Первый прогон — с глубины окна правила; дальше — от курсора.
        eval_from = cursor_ts or (now - timedelta(days=config.WINDOW_DAYS))
        window_start = eval_from - _CONTEXT

        raw = await self.source.load_raw_buckets(
            station.sdmo_id,
            window_start,
            now,
        )
        # Формирующаяся корзина не оценивается — иначе флаги будут дёргаться.
        raw = [b for b in raw if b.start_ts + _BUCKET_SPAN <= now]
        series = bucketizer.build_series(raw)

        base_moment = await self.source.get_base_moment(
            station.sdmo_id,
            window_start,
            now,
        )
        active = await self.incident_repo.get_active(
            detector_code=DETECTOR_CODE,
            well_id=station.well_id,
            reason_code=incident_config.REASON_ROD_BREAK,
        )

        actions = evaluate_episode(
            series,
            cursor_ts=cursor_ts,
            active_level=active.level if active else None,
            base_moment=base_moment,
        )

        opened = escalated = normalized = 0
        active_opened_at = active.opened_at if active else None
        active_detected_at = active.detected_at if active else None
        # Перечитывание истории (сброшенный курсор): эпизоды, уже записанные и
        # закрытые, распознаются по детерминированному opened_at и не пишутся
        # повторно — все их действия до нормализации включительно подавляются.
        suppress = False
        for action in actions:
            if action.kind in (ACTION_OPEN_WARNING, ACTION_OPEN_ALARM):
                existing = await self.incident_repo.get_by_opened(
                    detector_code=DETECTOR_CODE,
                    well_id=station.well_id,
                    reason_code=incident_config.REASON_ROD_BREAK,
                    opened_at=action.at,
                )
                suppress = (
                    existing is not None
                    and existing.status == INCIDENT_STATUS_NORMALIZED
                )
                if suppress:
                    continue
                active_opened_at = action.at
                active_detected_at = now
                opened += 1
            elif suppress:
                continue

            if action.kind == ACTION_NORMALIZE:
                await self.incident_repo.normalize(
                    detector_code=DETECTOR_CODE,
                    well_id=station.well_id,
                    reason_code=incident_config.REASON_ROD_BREAK,
                    normalized_at=action.at,
                )
                active_opened_at = None
                active_detected_at = None
                normalized += 1
                continue

            if action.kind == ACTION_ESCALATE:
                escalated += 1

            await self.incident_repo.upsert_active(
                self._build_upsert(
                    station=station,
                    action=action,
                    series=series,
                    base_moment=base_moment,
                    now=now,
                    opened_at=active_opened_at or action.at,
                    detected_at=active_detected_at or now,
                ),
            )

        last_complete = series[-1].start_ts + _BUCKET_SPAN if series else None
        await self.cursor_repo.upsert(
            detector_code=DETECTOR_CODE,
            entity_id=station.sdmo_id,
            last_event_at=last_complete or cursor_ts or now,
            last_run_at=now,
        )
        return opened, escalated, normalized

    def _build_upsert(  # noqa: PLR0913
        self,
        *,
        station: SdmoStation,
        action: EpisodeAction,
        series: list[Bucket2h],
        base_moment: float | None,
        now: datetime,
        opened_at: datetime,
        detected_at: datetime,
    ) -> OpenIncidentDTO:
        is_alarm = action.kind in (ACTION_OPEN_ALARM, ACTION_ESCALATE)
        level = INCIDENT_LEVEL_ALARM if is_alarm else INCIDENT_LEVEL_WARNING
        # Эскалация датируется временем ДАННЫХ (корзина, замкнувшая алармный
        # сустейн), а не настенными часами — при догоне истории это разное.
        escalated_at = None
        if action.kind == ACTION_ESCALATE:
            escalated_at = action.at
        elif action.kind == ACTION_OPEN_ALARM:
            escalated_at = action.at + config.SUSTAIN_BUCKETS * _BUCKET_SPAN
        # last_seen: для confirm — правая граница аномальной корзины, для
        # open/escalate — момент события.
        last_seen_at = (
            action.at
            if action.kind == ACTION_CONFIRM
            else max(
                action.at,
                opened_at,
            )
        )

        payload = self._build_payload(
            series=series,
            base_moment=base_moment,
            until=last_seen_at,
            with_failure=is_alarm,
            opened_at=opened_at,
            now=now,
        )
        return OpenIncidentDTO(
            detector_code=DETECTOR_CODE,
            well_id=station.well_id,
            entity_id=station.sdmo_id,
            reason_code=incident_config.REASON_ROD_BREAK,
            level=level,
            opened_at=opened_at,
            detected_at=detected_at,
            last_seen_at=last_seen_at,
            escalated_at=escalated_at,
            config_version=config.CONFIG_VERSION,
            payload=payload,
        )

    @staticmethod
    def _build_payload(  # noqa: PLR0913
        *,
        series: list[Bucket2h],
        base_moment: float | None,
        until: datetime,
        with_failure: bool,
        opened_at: datetime,
        now: datetime,
    ) -> dict:
        recent = [b for b in series if b.start_ts < until]
        recent = recent[-incident_config.EVIDENCE_BUCKETS :]
        last = recent[-1] if recent else None

        payload: dict = {
            "base_moment": base_moment,
            "warn_threshold": incident_config.WARN_MOM_DROP_RATIO,
            "alarm_threshold": config.MOM_DROP_RATIO,
            "current_ratio": (
                round(last.mom_min / last.mom_med24, 3)
                if last and last.mom_med24
                else None
            ),
            "evidence": [
                {
                    "bucket": b.start_ts.isoformat(),
                    "mom_min": b.mom_min,
                    "spd": b.spd,
                    "mom_med24": b.mom_med24,
                }
                for b in recent
            ],
        }
        if with_failure:
            recovered = failure_dt.recover_failure_dt(series, base_moment)
            payload["failure_dt"] = recovered.isoformat() if recovered else None
            payload["event_class"] = failure_dt.classify_event(recovered, now)
            if recovered is not None:
                delta = recovered - opened_at
                payload["lead_time_hours"] = round(
                    delta.total_seconds() / 3600,
                    1,
                )
            payload["low_confidence"] = (
                base_moment is not None and base_moment < config.MIN_BASE_MOMENT
            )
        return payload


async def main(entity_ids: list[int] | None = None) -> None:
    async with session_makers["app"]() as session:
        await RodBreakIncidentRunner(session).run(entity_ids)


@celery_app.task(name="detectors.rod_breaks.run_incidents")
def run_rod_break_incidents(entity_ids: list[int] | None = None) -> None:
    run_async(main(entity_ids))


if __name__ == "__main__":
    asyncio.run(main())
