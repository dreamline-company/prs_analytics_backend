"""Проверка эпизодов R2 независимыми данными — ежечасно в :30.

Берёт эпизоды R2 за последние ``FINAL_DAYS`` суток и старые неокончательные,
по каждому заново считает отметку (``verification.rule.evaluate``) и
записывает её в ``detectors_verification``; смена отметки, причины или
окончательности — строка в ``detectors_verification_history``. Время в :30 —
после статусов ABAI (:10) и замеров ЦИТС (:20).

Ручной запуск::

    python -m apps.detectors.rod_breaks.tasks.verify.verify --dry-run
    python -m apps.detectors.rod_breaks.tasks.verify.verify --backfill-days 60
    python -m apps.detectors.rod_breaks.tasks.verify.verify --incident-ids 1170,818
"""

import argparse
import asyncio
from collections import Counter
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.dto.internal.repositories.verification import (
    CreateVerificationDTO,
    CreateVerificationHistoryDTO,
    UpdateVerificationDTO,
)
from apps.detectors.models.incident import DetectorIncident
from apps.detectors.models.verification import (
    VERDICT_FAILURE_CONFIRMED,
    DetectorVerification,
)
from apps.detectors.repositories import (
    DetectorVerificationHistoryRepository,
    DetectorVerificationRepository,
)
from apps.detectors.rod_breaks.verification import config
from apps.detectors.rod_breaks.verification.data_source import (
    IncidentRef,
    R2VerificationSource,
)
from apps.detectors.rod_breaks.verification.rule import VerificationResult, evaluate
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

DETECTOR_CODE = "R2"


@dataclass(frozen=True, slots=True)
class VerificationRef:
    """Снимок текущей отметки: ORM-объект после rollback/commit протухает."""

    id: int
    verdict: str
    reason: str | None
    is_final: bool
    evidence_at: datetime | None
    decided_at: datetime
    final_at: datetime | None
    evidence: dict | None

    @classmethod
    def of(cls, row: DetectorVerification) -> "VerificationRef":
        return cls(
            id=row.id,
            verdict=row.verdict,
            reason=row.reason,
            is_final=row.is_final,
            evidence_at=row.evidence_at,
            decided_at=row.decided_at,
            final_at=row.final_at,
            evidence=row.evidence,
        )


def local_now() -> datetime:
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


class R2VerificationRunner:
    def __init__(self, session: AsyncSession, *, dry_run: bool = False) -> None:
        self.session = session
        self.dry_run = dry_run
        self.source = R2VerificationSource(session)
        self.verifications = DetectorVerificationRepository(session)
        self.history = DetectorVerificationHistoryRepository(session)

    async def run(
        self,
        *,
        incident_ids: list[int] | None = None,
        backfill_days: int | None = None,
    ) -> Counter:
        now = local_now()
        incidents = await self._targets(now, incident_ids, backfill_days)
        existing = {
            incident_id: VerificationRef.of(row)
            for incident_id, row in (
                await self.verifications.get_map_by_incident_ids(
                    [incident.id for incident in incidents],
                )
            ).items()
        }
        stats: Counter = Counter()
        for incident in incidents:
            try:
                data = await self.source.load(incident)
                result = evaluate(data, now)
                change = await self._save(
                    incident,
                    existing.get(incident.id),
                    result,
                    now,
                )
                if self.dry_run:
                    await self.session.rollback()
                else:
                    await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception("R2 verify: incident %s failed", incident.id)
                stats["failed"] += 1
                continue
            stats[result.verdict] += 1
            if change:
                stats[f"changed:{change}"] += 1
            if self.dry_run:
                logger.info(
                    "R2 verify (dry-run) incident=%s well=%s t0=%s -> %s / %s",
                    incident.id,
                    incident.well_id,
                    incident.opened_at,
                    result.verdict,
                    result.reason,
                )
        logger.info(
            "R2 verify: incidents=%s %s",
            len(incidents),
            dict(sorted(stats.items())),
        )
        return stats

    async def _targets(
        self,
        now: datetime,
        incident_ids: list[int] | None,
        backfill_days: int | None,
    ) -> list[IncidentRef]:
        """Свежие эпизоды R2 и неокончательные старые (их окончательно закрыть).

        ``backfill_days`` — дополнительно эпизоды за столько суток без отметки
        (первичное заполнение истории).
        """
        recent = DetectorIncident.opened_at >= now - timedelta(days=config.FINAL_DAYS)
        not_final = and_(
            DetectorVerification.id.is_not(None),
            DetectorVerification.is_final.is_(False),
        )
        conditions = [recent, not_final]
        if backfill_days is not None:
            conditions.append(
                and_(
                    DetectorVerification.id.is_(None),
                    DetectorIncident.opened_at >= now - timedelta(days=backfill_days),
                ),
            )
        stmt = (
            select(
                DetectorIncident.id,
                DetectorIncident.detector_code,
                DetectorIncident.well_id,
                DetectorIncident.entity_id,
                DetectorIncident.opened_at,
            )
            .outerjoin(
                DetectorVerification,
                DetectorVerification.incident_id == DetectorIncident.id,
            )
            .where(
                DetectorIncident.detector_code == DETECTOR_CODE,
                DetectorIncident.opened_at <= now,
                or_(*conditions),
            )
            .order_by(DetectorIncident.opened_at)
        )
        if incident_ids:
            stmt = stmt.where(DetectorIncident.id.in_(incident_ids))
        rows = (await self.session.execute(stmt)).all()
        return [IncidentRef(*row) for row in rows]

    async def _save(
        self,
        incident: IncidentRef,
        current: VerificationRef | None,
        result: VerificationResult,
        now: datetime,
    ) -> str | None:
        """Записать отметку; вернуть «было->стало», если она сменилась."""
        if current is None:
            row = await self.verifications.create(
                CreateVerificationDTO(
                    incident_id=incident.id,
                    well_id=incident.well_id,
                    detector_code=incident.detector_code,
                    verdict=result.verdict,
                    reason=result.reason,
                    is_final=result.is_final,
                    evidence_at=result.evidence_at,
                    decided_at=now,
                    final_at=now if result.is_final else None,
                    evidence=result.evidence,
                    rule_version=config.RULE_VERSION,
                ),
            )
            await self._log(row.id, None, None, result, now)
            return f"new->{result.verdict}"

        # Окончательная отметка не меняется. Подтверждённый отказ ещё
        # дополняется (позже пришёл ремонт «обрыв»), пока не истёк срок.
        if current.is_final and current.verdict != VERDICT_FAILURE_CONFIRMED:
            return None
        if (
            current.verdict == VERDICT_FAILURE_CONFIRMED
            and result.verdict != VERDICT_FAILURE_CONFIRMED
        ):
            logger.warning(
                "R2 verify: incident %s lost confirmation (repair/status removed "
                "in ABAI?) — verdict kept",
                incident.id,
            )
            return None

        changed = (
            current.verdict != result.verdict
            or current.reason != result.reason
            or current.is_final != result.is_final
        )
        if changed:
            await self.verifications.update(
                UpdateVerificationDTO(
                    verdict=result.verdict,
                    reason=result.reason,
                    is_final=result.is_final,
                    evidence_at=result.evidence_at,
                    decided_at=now
                    if current.verdict != result.verdict
                    else current.decided_at,
                    final_at=now
                    if result.is_final and not current.is_final
                    else current.final_at,
                    evidence=result.evidence,
                    rule_version=config.RULE_VERSION,
                ),
                filters=(DetectorVerification.id == current.id,),
            )
            await self._log(current.id, current.verdict, current.reason, result, now)
            return f"{current.verdict}->{result.verdict}"

        if (
            current.evidence != result.evidence
            or current.evidence_at != result.evidence_at
        ):
            await self.verifications.update(
                UpdateVerificationDTO(
                    evidence=result.evidence,
                    evidence_at=result.evidence_at,
                ),
                filters=(DetectorVerification.id == current.id,),
            )
        return None

    async def _log(
        self,
        verification_id: int,
        verdict_from: str | None,
        reason_from: str | None,
        result: VerificationResult,
        now: datetime,
    ) -> None:
        await self.history.create(
            CreateVerificationHistoryDTO(
                verification_id=verification_id,
                verdict_from=verdict_from,
                verdict_to=result.verdict,
                reason_from=reason_from,
                reason_to=result.reason,
                is_final=result.is_final,
                evidence=result.evidence,
                changed_at=now,
                rule_version=config.RULE_VERSION,
            ),
        )


async def main(
    *,
    incident_ids: list[int] | None = None,
    backfill_days: int | None = None,
    dry_run: bool = False,
) -> None:
    async with session_makers["app"]() as session:
        await R2VerificationRunner(session, dry_run=dry_run).run(
            incident_ids=incident_ids,
            backfill_days=backfill_days,
        )


@celery_app.task(name="detectors.rod_breaks.verify")
def verify_rod_break_incidents() -> None:
    run_async(main())


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="R2: проверка эпизодов")
    parser.add_argument("--incident-ids", type=str, default=None)
    parser.add_argument("--backfill-days", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    ids = [int(x) for x in args.incident_ids.split(",")] if args.incident_ids else None
    asyncio.run(
        main(incident_ids=ids, backfill_days=args.backfill_days, dry_run=args.dry_run),
    )
