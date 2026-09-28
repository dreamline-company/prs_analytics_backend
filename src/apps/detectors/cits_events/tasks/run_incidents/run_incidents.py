"""Суточный прогон R10: состояние скважин НГДУ на дату -> эпизоды и срез.

На каждую дату фиксации (закрытые местные сутки):

1. вход правила из копий — замеры ЦИТС за 75 суток, техрежим, статусы ABAI;
2. первый проход правила — какие сутки СУ нужны; выборка регистра 1998;
3. второй проход — состояние каждой скважины;
4. события 1/2 -> эпизоды ``detectors_incident`` (решение — services/episode);
5. события 3/4 и сервисный перечень -> срез ``detectors_finding`` за дату;
6. курсор НГДУ (``detectors_cursor``, entity_id = ABAI id НГДУ).

Всё за дату пишется одной транзакцией. Курсор помнит последнюю оценённую
дату: пропущенные beat-ом сутки догоняются по порядку (не больше
CATCHUP_DAYS), потому что эпизод следующих суток зависит от предыдущих.

    python -m apps.detectors.cits_events.tasks.run_incidents.run_incidents
    python -m apps.detectors.cits_events.tasks.run_incidents.run_incidents \\
        --date 2026-09-18

Явная дата — ручной прогон: эпизоды обновляются так, будто это текущие
сутки, поэтому прошлые даты гонять только по порядку и на пустом R10.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.cits_events import config, incident_config
from apps.detectors.cits_events.rule import (
    EVENT_NULL,
    EVENT_STALE,
    SERVICE_NULL_LIQUID,
    Event,
    MappingSu,
    WellVerdict,
    evaluate,
    su_requests,
)
from apps.detectors.cits_events.services.data_source import (
    CitsEventsDataSource,
    WellRecord,
)
from apps.detectors.cits_events.services.episode import (
    ACTION_CLOSE,
    ACTION_UPSERT,
    decide,
)
from apps.detectors.dto.internal.repositories import (
    CreateDetectorFindingDTO,
    OpenIncidentDTO,
)
from apps.detectors.models.incident import (
    CLOSE_REASON_STALE,
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_ACTIVE,
    DetectorIncident,
)
from apps.detectors.repositories import (
    DetectorCursorRepository,
    DetectorFindingRepository,
    DetectorIncidentRepository,
)
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

logger = get_logger(__name__)
settings = get_settings()

DETECTOR_CODE = "R10"


def _day_start(day: date) -> datetime:
    return datetime.combine(day, time.min)


def _day_end(day: date) -> datetime:
    """Правая граница суток — полночь следующих."""
    return datetime.combine(day + timedelta(days=1), time.min)


def _local_now() -> datetime:
    return datetime.now(settings.ZONE_INFO).replace(tzinfo=None)


def _round(value: float | None, digits: int = 3) -> float | None:
    return round(value, digits) if value is not None else None


def _iso(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


def _event_payload(event: Event) -> dict:
    return {
        "event": event.event,
        "klass": event.klass,
        "last_day": _iso(event.last_day),
        "age_d": event.age_d,
        "q_last": _round(event.q_last),
        "rezhim": event.rezhim,
        "dev": _round(event.dev),
        "n_series": event.n_series,
        "series_from": _iso(event.series_from),
        "prev_dev": _round(event.prev_dev),
        "su": event.su,
        "note": event.note,
    }


@dataclass(slots=True)
class DayStats:
    opened: int = 0
    escalated: int = 0
    normalized: int = 0
    findings: int = 0


class CitsEventsRunner:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.source = CitsEventsDataSource(session)
        self.incident_repo = DetectorIncidentRepository(session)
        self.finding_repo = DetectorFindingRepository(session)
        self.cursor_repo = DetectorCursorRepository(session)

    async def run(self, *, fix_date: date | None = None) -> None:
        now = _local_now()
        target = fix_date or now.date() - timedelta(days=1)
        for ngdu in incident_config.TARGET_NGDU_IDS:
            days = [target] if fix_date else await self._pending_days(ngdu, target)
            for day in days:
                try:
                    stats = await self._run_day(int(ngdu), day, now)
                    await self.session.commit()
                except Exception:
                    await self.session.rollback()
                    # Следующие сутки опираются на эпизоды этих — дальше не идём.
                    logger.exception("R10 NGDU %s day %s failed", ngdu, day)
                    break
                logger.info(
                    "R10 NGDU %s %s: opened=%s, escalated=%s, normalized=%s, "
                    "findings=%s",
                    int(ngdu),
                    day,
                    stats.opened,
                    stats.escalated,
                    stats.normalized,
                    stats.findings,
                )

    async def _pending_days(self, ngdu: int, target: date) -> list[date]:
        cursors = await self.cursor_repo.get_map(DETECTOR_CODE, [int(ngdu)])
        cursor = cursors.get(int(ngdu))
        if cursor is None:
            return [target]
        # Курсор хранит правую границу — полночь после последних суток.
        done = cursor.last_event_at.date() - timedelta(days=1)
        first = max(
            done + timedelta(days=1),
            target - timedelta(days=incident_config.CATCHUP_DAYS - 1),
        )
        return [first + timedelta(days=i) for i in range((target - first).days + 1)]

    async def _run_day(self, ngdu: int, day: date, now: datetime) -> DayStats:
        records = await self.source.load_wells(day, ngdu)
        su_data = await self.source.load_su(
            su_requests([record.well for record in records], day),
            ngdu,
        )
        verdicts = {
            record.well_id: evaluate(
                record.well,
                day,
                MappingSu(su_data.get(record.well.name, {})),
            )
            for record in records
        }

        stats = DayStats()
        active = await self._active_incidents()
        for record in records:
            await self._apply_episode(
                record=record,
                verdict=verdicts[record.well_id],
                active=active.pop(record.well_id, None),
                ngdu=ngdu,
                day=day,
                now=now,
                stats=stats,
            )
        # Эпизоды скважин НГДУ, у которых в окне не осталось ни одного замера.
        for well_id, incident in active.items():
            if (incident.payload or {}).get("abai_ngdu_id") != ngdu:
                continue
            await self._close(well_id, day, CLOSE_REASON_STALE)
            stats.normalized += 1

        findings = [
            finding
            for record in records
            for finding in self._findings(record, verdicts[record.well_id], day)
        ]
        await self.finding_repo.replace_for_date(
            detector_code=DETECTOR_CODE,
            fix_date=day,
            rows=findings,
        )
        stats.findings = len(findings)

        await self.cursor_repo.upsert(
            detector_code=DETECTOR_CODE,
            entity_id=ngdu,
            last_event_at=_day_end(day),
            last_run_at=now,
        )
        return stats

    async def _active_incidents(self) -> dict[int, DetectorIncident]:
        rows = await self.incident_repo.get_list(
            QuerySpec(
                filters=(
                    DetectorIncident.detector_code == DETECTOR_CODE,
                    DetectorIncident.reason_code == incident_config.REASON_LIQUID_LOSS,
                    DetectorIncident.status == INCIDENT_STATUS_ACTIVE,
                ),
            ),
        )
        return {row.well_id: row for row in rows}

    async def _apply_episode(  # noqa: PLR0913
        self,
        *,
        record: WellRecord,
        verdict: WellVerdict,
        active: DetectorIncident | None,
        ngdu: int,
        day: date,
        now: datetime,
        stats: DayStats,
    ) -> None:
        last_seen = active.last_seen_at.date() - timedelta(days=1) if active else None
        decision = decide(verdict, fix=day, active_last_seen=last_seen)

        if decision.action == ACTION_CLOSE:
            await self._close(record.well_id, day, decision.close_reason)
            stats.normalized += 1
            return
        if decision.action != ACTION_UPSERT:
            return

        event = verdict.incident_event
        escalates = (
            active is not None
            and active.level == INCIDENT_LEVEL_WARNING
            and decision.level == INCIDENT_LEVEL_ALARM
        )
        await self.incident_repo.upsert_active(
            OpenIncidentDTO(
                detector_code=DETECTOR_CODE,
                well_id=record.well_id,
                reason_code=incident_config.REASON_LIQUID_LOSS,
                level=decision.level,
                # Начало серии — физическое начало эпизода; у открытого эпизода
                # upsert его не трогает.
                opened_at=_day_start(event.series_from or day),
                detected_at=now,
                last_seen_at=_day_end(day),
                escalated_at=_day_end(day) if escalates else None,
                config_version=config.CONFIG_VERSION,
                payload={
                    "abai_ngdu_id": ngdu,
                    "fix_date": day.isoformat(),
                    **_event_payload(event),
                },
            ),
        )
        if active is None:
            stats.opened += 1
        elif escalates:
            stats.escalated += 1

    async def _close(self, well_id: int, day: date, reason: str | None) -> None:
        await self.incident_repo.normalize(
            detector_code=DETECTOR_CODE,
            well_id=well_id,
            reason_code=incident_config.REASON_LIQUID_LOSS,
            normalized_at=_day_end(day),
            close_reason=reason,
        )

    @staticmethod
    def _findings(
        record: WellRecord,
        verdict: WellVerdict,
        day: date,
    ) -> list[CreateDetectorFindingDTO]:
        rows = []
        event = verdict.event
        if event is not None and event.event in {EVENT_STALE, EVENT_NULL}:
            rows.append(
                CreateDetectorFindingDTO(
                    detector_code=DETECTOR_CODE,
                    fix_date=day,
                    well_id=record.well_id,
                    kind=(
                        incident_config.FINDING_STALE
                        if event.event == EVENT_STALE
                        else incident_config.FINDING_NULL_LIQUID
                    ),
                    title=event.klass,
                    config_version=config.CONFIG_VERSION,
                    payload=_event_payload(event),
                ),
            )
        for item in verdict.service:
            # Пустой замер уже записан строкой события 4.
            if item.kind == SERVICE_NULL_LIQUID:
                continue
            rows.append(
                CreateDetectorFindingDTO(
                    detector_code=DETECTOR_CODE,
                    fix_date=day,
                    well_id=record.well_id,
                    kind=item.kind,
                    title=item.title,
                    config_version=config.CONFIG_VERSION,
                    payload={"detail": item.detail},
                ),
            )
        return rows


async def main(fix_date: date | None = None) -> None:
    async with session_makers["app"]() as session:
        await CitsEventsRunner(session).run(fix_date=fix_date)


@celery_app.task(name="detectors.cits_events.run_incidents")
def run_cits_events_incidents(
    entity_ids: list[int] | None = None,
    fix_date: str | None = None,
) -> None:
    """``entity_ids`` — подпись диспетчера; R10 считает НГДУ целиком."""
    _ = entity_ids
    run_async(main(date.fromisoformat(fix_date) if fix_date else None))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="Дата фиксации (закрытые сутки). По умолчанию — вчера.",
    )
    args = parser.parse_args()
    asyncio.run(main(args.date))
