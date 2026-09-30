"""Курсорный прогон R9: суточная детекция перекоса нагрузки на фонде ШГН.

Правило суточное, поэтому раннер бежит раз в сутки (beat), а не на каждый
инкремент телеметрии. По каждой целевой станции: курсор -> суточные агрегаты
с 60-дневным фоновым контекстом -> каузальный прогон правила -> state machine
эпизода -> upsert инцидентов -> сдвиг курсора. Инциденты и курсор пишутся в
одной транзакции; повторная обработка того же окна идемпотентна (частичный
уникальный индекс инцидентов + детерминированный opened_at).
"""

import asyncio
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.conclusion.notify import notify_conclusion
from apps.detectors.dto.internal.repositories.incident import OpenIncidentDTO
from apps.detectors.load_imbalance import config, incident_config, rule
from apps.detectors.load_imbalance.dto.internal.day import (
    STATE_UNDETERMINED,
    DayAggregate,
    DayVerdict,
)
from apps.detectors.load_imbalance.services.episode import (
    ACTION_ESCALATE,
    ACTION_NORMALIZE,
    ACTION_OPEN_WARNING,
    EpisodeAction,
    evaluate_episode,
)
from apps.detectors.load_imbalance.services.telemetry_source import (
    LoadImbalanceTelemetrySource,
)
from apps.detectors.models.incident import (
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_NORMALIZED,
    DetectorIncident,
)
from apps.detectors.repositories import (
    DetectorCursorRepository,
    DetectorIncidentRepository,
)
from apps.models_registry import *  # noqa: F403
from apps.telemetry.models.sdmo import SdmoStation
from apps.telemetry.repositories.sdmo import SdmoStationRepository
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)

DETECTOR_CODE = "R9"


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def _day_end(day: date) -> datetime:
    """Правая граница суток — полночь следующих."""
    return datetime.combine(day + timedelta(days=1), time.min)


async def target_stations(
    session: AsyncSession,
    entity_ids: list[int] | None = None,
) -> list[SdmoStation]:
    """Целевые станции: ШГН с привязкой к скважине, одна станция на скважину.

    У скважины бывает несколько станций — берётся станция с минимальным
    локальным ``id``, чтобы эпизоды скважины всегда считались по одной ленте.
    Локальный ``id`` — ключ станции и в fc_data, и в курсорах (``entity_id``).
    """
    filters = [
        SdmoStation.type_1900 == config.TARGET_TYPE_1900,
        SdmoStation.well_id.is_not(None),
    ]
    if entity_ids:
        filters.append(SdmoStation.id.in_(entity_ids))

    stations = await SdmoStationRepository(session).get_list(
        QuerySpec(filters=tuple(filters), order_by=(SdmoStation.id,)),
    )
    by_well: dict[int, SdmoStation] = {}
    for station in stations:
        by_well.setdefault(station.well_id, station)
    return sorted(by_well.values(), key=lambda station: station.id)


class LoadImbalanceIncidentRunner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.source = LoadImbalanceTelemetrySource(session)
        self.incident_repo = DetectorIncidentRepository(session)
        self.cursor_repo = DetectorCursorRepository(session)

    async def run(self, entity_ids: list[int] | None = None) -> None:
        stations = await target_stations(self.session, entity_ids)
        cursors = await self.cursor_repo.get_map(
            DETECTOR_CODE,
            [station.id for station in stations] or None,
        )

        opened = escalated = normalized = 0
        for station in stations:
            cursor = cursors.get(station.id)
            try:
                counts = await self._run_station(
                    station,
                    cursor_ts=cursor.last_event_at if cursor else None,
                )
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception(
                    "R9 station %s failed; cursor kept",
                    station.id,
                )
                continue
            opened += counts[0]
            escalated += counts[1]
            normalized += counts[2]
            # Открытие/эскалация меняют уровень эпизода — будим ИИ-заключение.
            if counts[0] or counts[1]:
                await self._notify_conclusion(station.well_id)

        if opened or escalated or normalized:
            logger.info(
                "R9 incidents: opened=%s, escalated=%s, normalized=%s (stations=%s)",
                opened,
                escalated,
                normalized,
                len(stations),
            )

    async def _notify_conclusion(self, well_id: int) -> None:
        active = await self.incident_repo.get_active(
            detector_code=DETECTOR_CODE,
            well_id=well_id,
            reason_code=incident_config.REASON_LOAD_IMBALANCE,
        )
        if active is not None:
            notify_conclusion(active.id)

    async def _run_station(
        self,
        station: SdmoStation,
        *,
        cursor_ts: datetime | None,
    ) -> tuple[int, int, int]:
        # Местное время: сутки СДМО (savetime) и расписание — по Атырау; по
        # UTC в 04:10 «сегодня» ещё вчера, и вчерашние сутки не оценивались.
        now = datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        as_of = now.date()
        # Курсор хранит правую границу обработанного (полночь следующих суток),
        # поэтому последние оценённые сутки — на день раньше.
        cursor_day = cursor_ts.date() - timedelta(days=1) if cursor_ts else None

        data_front = await self.source.get_data_front(station.id)
        if data_front is None:
            return (0, 0, 0)

        eval_from = (
            cursor_day + timedelta(days=1)
            if cursor_day
            else as_of - timedelta(days=config.HISTORY_DAYS)
        )
        # Неполные сутки не оцениваются: последние сутки с данными почти всегда
        # половинчатые, а порога в 100 снимков для защиты мало — он набегает за
        # несколько часов работы.
        eval_until = min(as_of, data_front) - timedelta(days=1)
        if eval_until < eval_from:
            return (0, 0, 0)

        window_start = eval_from - timedelta(days=config.BASE_WINDOW_DAYS)
        days = await self.source.load_daily(
            station.id,
            window_start,
            eval_until,
        )
        verdicts = rule.evaluate_series(days)

        active = await self.incident_repo.get_active(
            detector_code=DETECTOR_CODE,
            well_id=station.well_id,
            reason_code=incident_config.REASON_LOAD_IMBALANCE,
        )
        actions = evaluate_episode(
            verdicts,
            cursor_day=cursor_day,
            active_level=active.level if active else None,
        )

        opened, escalated, normalized, is_open = await self._apply(
            station=station,
            actions=actions,
            verdicts=verdicts,
            active=active,
            now=now,
        )
        if is_open:
            normalized += await self._close_if_stale(
                station=station,
                days=days,
                as_of=as_of,
                fallback=active.last_seen_at.date() if active else None,
            )

        await self.cursor_repo.upsert(
            detector_code=DETECTOR_CODE,
            entity_id=station.id,
            last_event_at=_day_end(eval_until),
            last_run_at=now,
        )
        return (opened, escalated, normalized)

    async def _apply(
        self,
        *,
        station: SdmoStation,
        actions: list[EpisodeAction],
        verdicts: list[DayVerdict],
        active: DetectorIncident | None,
        now: datetime,
    ) -> tuple[int, int, int, bool]:
        """Записать действия state machine. Возвращает счётчики и «эпизод открыт»."""
        opened = escalated = normalized = 0
        opened_at = active.opened_at if active else None
        detected_at = active.detected_at if active else None
        is_open = active is not None
        # Перечитывание истории (сброшенный курсор): уже записанные и закрытые
        # эпизоды распознаются по детерминированному opened_at и не пишутся
        # повторно — их действия до нормализации включительно подавляются.
        suppress = False

        for action in actions:
            if action.kind == ACTION_OPEN_WARNING:
                existing = await self.incident_repo.get_by_opened(
                    detector_code=DETECTOR_CODE,
                    well_id=station.well_id,
                    reason_code=incident_config.REASON_LOAD_IMBALANCE,
                    opened_at=_day_start(action.day),
                )
                suppress = (
                    existing is not None
                    and existing.status == INCIDENT_STATUS_NORMALIZED
                )
                if suppress:
                    continue
                opened_at = _day_start(action.day)
                detected_at = now
                is_open = True
                opened += 1
            elif suppress:
                continue

            if action.kind == ACTION_NORMALIZE:
                await self.incident_repo.normalize(
                    detector_code=DETECTOR_CODE,
                    well_id=station.well_id,
                    reason_code=incident_config.REASON_LOAD_IMBALANCE,
                    normalized_at=_day_end(action.day),
                )
                opened_at = detected_at = None
                is_open = False
                normalized += 1
                continue

            if action.kind == ACTION_ESCALATE:
                escalated += 1

            await self.incident_repo.upsert_active(
                self._build_upsert(
                    station=station,
                    action=action,
                    verdicts=verdicts,
                    opened_at=opened_at or _day_start(action.day),
                    detected_at=detected_at or now,
                ),
            )

        return (opened, escalated, normalized, is_open)

    async def _close_if_stale(
        self,
        *,
        station: SdmoStation,
        days: list[DayAggregate],
        as_of: date,
        fallback: date | None,
    ) -> int:
        """Закрыть повисший эпизод, если валидных суток давно нет.

        Пока станция молчит (снята, идёт ПРС, умер канал), сутки уходят в
        «не определено»: ни сработки, ни восстановления, и эпизод висел бы
        вечно.
        """
        last_valid = max(
            (day.day for day in days if rule.is_valid(day)),
            default=fallback,
        )
        if last_valid is None:
            return 0
        if (as_of - last_valid).days <= incident_config.STALE_DAYS:
            return 0

        await self.incident_repo.normalize(
            detector_code=DETECTOR_CODE,
            well_id=station.well_id,
            reason_code=incident_config.REASON_LOAD_IMBALANCE,
            normalized_at=_day_end(last_valid),
            close_reason=CLOSE_REASON_STALE,
        )
        return 1

    @staticmethod
    def _build_upsert(
        *,
        station: SdmoStation,
        action: EpisodeAction,
        verdicts: list[DayVerdict],
        opened_at: datetime,
        detected_at: datetime,
    ) -> OpenIncidentDTO:
        is_alarm = action.kind == ACTION_ESCALATE
        level = INCIDENT_LEVEL_ALARM if is_alarm else INCIDENT_LEVEL_WARNING
        last_seen_at = _day_end(action.day)
        return OpenIncidentDTO(
            detector_code=DETECTOR_CODE,
            well_id=station.well_id,
            entity_id=station.id,
            reason_code=incident_config.REASON_LOAD_IMBALANCE,
            level=level,
            opened_at=opened_at,
            detected_at=detected_at,
            last_seen_at=last_seen_at,
            escalated_at=_day_end(action.day) if is_alarm else None,
            config_version=config.CONFIG_VERSION,
            payload=_build_payload(verdicts, until=action.day),
        )


def _build_payload(verdicts: list[DayVerdict], *, until: date) -> dict:
    """Улики вокруг события: чем обосновано решение правила."""
    recent = [verdict for verdict in verdicts if verdict.day <= until]
    recent = recent[-incident_config.EVIDENCE_DAYS :]
    last = recent[-1] if recent else None
    baseline = last.baseline if last else None

    return {
        "k": round(last.k, 4) if last and last.k is not None else None,
        "branches": list(last.branches) if last else [],
        "k_alert": config.K_ALERT,
        "k_degrade": config.K_DEGRADE,
        "baseline": (
            {
                "n_days": baseline.n_days,
                "k_p90": round(baseline.k_p90, 4),
                "p95_median": (
                    round(baseline.p95_median, 1)
                    if baseline.p95_median is not None
                    else None
                ),
                "abs_branch_muted": baseline.k_p90 > config.BASE_P90_LIMIT,
            }
            if baseline
            else None
        ),
        "evidence": [
            {
                "day": verdict.day.isoformat(),
                "state": verdict.state,
                "n": verdict.n_samples,
                "k": round(verdict.k, 4) if verdict.k is not None else None,
                "p5": round(verdict.p5, 1),
                "p50": round(verdict.p50, 1),
                "p95": round(verdict.p95, 1),
            }
            for verdict in recent
            if verdict.state != STATE_UNDETERMINED or verdict.n_samples > 0
        ],
    }


async def main(entity_ids: list[int] | None = None) -> None:
    async with session_makers["app"]() as session:
        await LoadImbalanceIncidentRunner(session).run(entity_ids)


@celery_app.task(name="detectors.load_imbalance.run_incidents")
def run_load_imbalance_incidents(entity_ids: list[int] | None = None) -> None:
    run_async(main(entity_ids))


if __name__ == "__main__":
    asyncio.run(main())
