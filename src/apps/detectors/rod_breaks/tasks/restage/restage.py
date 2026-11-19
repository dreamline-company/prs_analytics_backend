"""Разовый пересчёт стадий старых эпизодов R2 по правилу 4 часов (19.11.2026).

До правила заказчика эпизод становился ``alarm`` через ~45 мин после провала
(порог 40 %). Теперь ``alarm`` = «Возможный обрыв штанг» — момент не вернулся
за 4 часа от T0. Скрипт для эпизодов R2, открытых за ``--days`` суток:

- считает замороженную базу (средний момент за сутки до T0 на рабочих
  отсчётах) и кладёт её в ``payload.base_t0`` — по ней живой прогон и закрывает;
- проверяет час T0 + 3…4 ч и далее до закрытия (у активного — до сейчас):
  момент не вернулся — ``level = alarm``, ``escalated_at`` = конец корзины
  проверки, ``payload.stage_check`` — цифры; вернулся — ``warning`` без
  эскалации (в т.ч. понижение активной «аварии» младше 4 ч).

Закрытия (``normalized_at``) не трогаются. Эпизод, который раннер R2 обновил
после снимка, пропускается (``skipped_changed``) — повторный запуск его
досчитает. Запуск::

    python -m apps.detectors.rod_breaks.tasks.restage.restage --days 90 --dry-run
    python -m apps.detectors.rod_breaks.tasks.restage.restage --days 90
    python -m apps.detectors.rod_breaks.tasks.restage.restage --incident-ids 4278
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.models.incident import (
    INCIDENT_LEVEL_ALARM,
    INCIDENT_LEVEL_WARNING,
    INCIDENT_STATUS_ACTIVE,
    DetectorIncident,
)
from apps.detectors.rod_breaks import config, incident_config
from apps.detectors.rod_breaks.services import bucketizer
from apps.detectors.rod_breaks.services.episode import frozen_base, stage_escalation
from apps.detectors.rod_breaks.services.telemetry_source import (
    RodBreakTelemetrySource,
)
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

DETECTOR_CODE = "R2"
_BASE_WINDOW = timedelta(hours=incident_config.BASE_HOURS)
_BUCKET_SPAN = timedelta(hours=config.BUCKET_HOURS)
_ESCALATE_AFTER = timedelta(hours=incident_config.STAGE_ESCALATE_HOURS)


@dataclass(frozen=True, slots=True)
class IncidentRow:
    """Снимок эпизода: ORM-объект после rollback протухает."""

    id: int
    well_id: int
    entity_id: int | None
    opened_at: datetime
    normalized_at: datetime | None
    level: str
    escalated_at: datetime | None
    payload: dict | None
    status: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class Restaged:
    level: str
    escalated_at: datetime | None
    payload: dict


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


class R2Restager:
    def __init__(self, session: AsyncSession, *, dry_run: bool = False) -> None:
        self.session = session
        self.dry_run = dry_run
        self.source = RodBreakTelemetrySource(session)

    async def run(
        self,
        *,
        days: int,
        incident_ids: list[int] | None = None,
    ) -> Counter:
        now = local_now()
        stmt = select(
            DetectorIncident.id,
            DetectorIncident.well_id,
            DetectorIncident.entity_id,
            DetectorIncident.opened_at,
            DetectorIncident.normalized_at,
            DetectorIncident.level,
            DetectorIncident.escalated_at,
            DetectorIncident.payload,
            DetectorIncident.status,
            DetectorIncident.updated_at,
        ).where(
            DetectorIncident.detector_code == DETECTOR_CODE,
            DetectorIncident.opened_at >= now - timedelta(days=days),
        )
        if incident_ids:
            stmt = stmt.where(DetectorIncident.id.in_(incident_ids))
        rows = [
            IncidentRow(*row)
            for row in (
                await self.session.execute(stmt.order_by(DetectorIncident.opened_at))
            ).all()
        ]
        await self.session.rollback()

        stats: Counter = Counter()
        for row in rows:
            try:
                result = await self._restage(row, now)
            except Exception:
                await self.session.rollback()
                logger.exception("R2 restage: incident %s failed", row.id)
                stats["failed"] += 1
                continue
            if result is None:
                stats["no_station"] += 1
                continue
            stats[f"{row.level}->{result.level}"] += 1
            if self.dry_run:
                logger.info(
                    "R2 restage (dry-run) incident=%s t0=%s %s/%s -> %s/%s base=%s",
                    row.id,
                    row.opened_at,
                    row.level,
                    row.escalated_at,
                    result.level,
                    result.escalated_at,
                    result.payload.get("base_t0"),
                )
                continue
            # Раннер R2 мог обновить эпизод после снимка — тогда не затираем.
            written = await self.session.execute(
                update(DetectorIncident)
                .where(
                    DetectorIncident.id == row.id,
                    DetectorIncident.updated_at == row.updated_at,
                )
                .values(
                    level=result.level,
                    escalated_at=result.escalated_at,
                    payload=result.payload,
                ),
            )
            await self.session.commit()
            if written.rowcount == 0:
                stats["skipped_changed"] += 1
        logger.info(
            "R2 restage: incidents=%s %s%s",
            len(rows),
            dict(sorted(stats.items())),
            " (dry-run)" if self.dry_run else "",
        )
        return stats

    async def _restage(self, row: IncidentRow, now: datetime) -> Restaged | None:
        if row.entity_id is None:
            return None
        until = row.normalized_at or now
        raw = await self.source.load_raw_buckets(
            row.entity_id,
            row.opened_at - _BASE_WINDOW - _BUCKET_SPAN,
            until,
        )
        series = bucketizer.build_series(raw)
        base, samples = frozen_base(series, row.opened_at)
        escalation = stage_escalation(
            series,
            opened_at=row.opened_at,
            until=until,
            base=base,
            base_samples=samples,
        )
        payload = {
            **(row.payload or {}),
            "base_t0": round(base, 1) if base is not None else None,
            "base_t0_samples": samples,
        }
        payload.pop("stage_check", None)
        if escalation is None:
            # Активная «авария» старше 4 ч без доказанного возврата (станция
            # молчит) остаётся аварией — понижаем только младше 4 ч.
            active_alarm = (
                row.status == INCIDENT_STATUS_ACTIVE
                and row.level == INCIDENT_LEVEL_ALARM
                and now >= row.opened_at + _ESCALATE_AFTER
            )
            if active_alarm:
                if row.payload and "stage_check" in row.payload:
                    payload["stage_check"] = row.payload["stage_check"]
                return Restaged(INCIDENT_LEVEL_ALARM, row.escalated_at, payload)
            return Restaged(INCIDENT_LEVEL_WARNING, None, payload)
        payload["stage_check"] = escalation.evidence
        return Restaged(INCIDENT_LEVEL_ALARM, escalation.at, payload)


async def main(
    *,
    days: int,
    incident_ids: list[int] | None = None,
    dry_run: bool = False,
) -> None:
    async with session_makers["app"]() as session:
        await R2Restager(session, dry_run=dry_run).run(
            days=days,
            incident_ids=incident_ids,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="R2: пересчёт стадий эпизодов")
    parser.add_argument("--days", type=int, default=90)
    parser.add_argument("--incident-ids", type=str, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ids = [int(x) for x in args.incident_ids.split(",")] if args.incident_ids else None
    asyncio.run(main(days=args.days, incident_ids=ids, dry_run=args.dry_run))
