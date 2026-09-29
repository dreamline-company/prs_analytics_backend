"""Рассылка суточных ведомостей R2/R9 по НГДУ на почту.

    python -m apps.detectors.tasks.mail_daily_sheets.mail_daily_sheets
    python -m apps.detectors.tasks.mail_daily_sheets.mail_daily_sheets \\
        --date 2026-09-20 --ngdu-id 5 [--resend] [--dry-run]

Celery ``detectors.daily_sheet.mail`` в 07:50 местного за вчерашние сутки —
после утренней сборки ведомостей (07:30). Ведомость берётся тем же use case,
что и API: готовая — из кэша, отсутствующая — собирается на месте, поэтому
рассылка не зависит от того, успела ли сборка. Одно письмо на НГДУ с
вложениями по обоим правилам; отправленное за дату письмо повторно не уходит
(журнал ``detectors_daily_sheet_delivery``), повтор — только ``--resend``.
Без SMTP_HOST письмо не отправляется, а логируется (dry run).
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.dto.internal.repositories.daily_sheet import (
    UpsertDailySheetDeliveryDTO,
)
from apps.detectors.dto.queries.daily_sheet import GetDailySheetQuery
from apps.detectors.models.daily_sheet import (
    DELIVERY_STATUS_FAILED,
    DELIVERY_STATUS_SENT,
    DELIVERY_STATUS_SKIPPED,
)
from apps.detectors.repositories.daily_sheet import (
    DetectorDailySheetDeliveryRepository,
)
from apps.detectors.services.daily_sheet.builder import local_now
from apps.detectors.services.daily_sheet.config import (
    SHEET_DETECTOR_CODES,
    sheet_applies,
)
from apps.detectors.services.daily_sheet.errors import DailySheetDataNotReadyError
from apps.detectors.services.daily_sheet.mail import (
    LetterInput,
    MissingSheet,
    SheetAttachment,
    attachment_filename,
    build_message,
    parse_recipients,
    parse_recipients_by_ngdu,
    recipients_for,
)
from apps.detectors.services.daily_sheet.targets import NgduRef, list_target_ngdus
from apps.detectors.use_cases.get_daily_sheet import (
    GetDailySheetUseCase,
    display_ngdu_name,
)
from apps.files.errors import FileMissingError
from apps.files.services.file import FileService
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage, FileNotExistError
from shared.database.sql.setup import session_makers
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_aioboto_presign_client_factory,
)
from shared.integrations.mail import SmtpMailer, smtp_config_from_settings

logger = get_logger(__name__)
settings = get_settings()

DAILY_SHEET_MAIL_TASK = "detectors.daily_sheet.mail"
_ERROR_MAX_CHARS = 1000


@dataclass(slots=True)
class MailStats:
    sent: int = 0
    skipped: int = 0
    failed: int = 0


class MailDailySheets:
    def __init__(
        self,
        *,
        sheet_date: date | None = None,
        ngdu_id: int | None = None,
        resend: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.sheet_date = sheet_date
        self.ngdu_id = ngdu_id
        self.resend = resend
        self.dry_run = dry_run

    async def run(self) -> MailStats:
        sheet_date = self.sheet_date or (local_now().date() - timedelta(days=1))
        stats = MailStats()
        default_to = parse_recipients(settings.DAILY_SHEET_MAIL_TO)
        by_ngdu = parse_recipients_by_ngdu(settings.DAILY_SHEET_MAIL_TO_BY_NGDU)
        smtp = smtp_config_from_settings(settings)
        if smtp is None and not self.dry_run:
            logger.warning(
                "Daily sheet mail: SMTP_HOST/SMTP_FROM not set — running as dry run",
            )
        mailer = SmtpMailer(smtp) if smtp is not None and not self.dry_run else None

        storage = AiobotoFileStorage(
            bucket_name=settings.S3_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
            presign_client_factory=get_aioboto_presign_client_factory(),
        )
        async with session_makers["app"]() as session:
            orgs = await list_target_ngdus(session, ngdu_id=self.ngdu_id)
            if not orgs:
                logger.warning(
                    "Daily sheet mail: no NGDU to send for (ngdu_id=%s)",
                    self.ngdu_id,
                )
                return stats
            for org in orgs:
                recipients = recipients_for(
                    org.abai_id,
                    default=default_to,
                    by_ngdu=by_ngdu,
                )
                try:
                    await self._send_one(
                        session,
                        storage,
                        mailer,
                        stats,
                        org=org,
                        sheet_date=sheet_date,
                        recipients=recipients,
                    )
                except Exception as exc:
                    # Сбой одного НГДУ (S3, БД) не должен лишать письма остальные.
                    await session.rollback()
                    stats.failed += 1
                    logger.exception(
                        "Daily sheet mail ngdu=%s date=%s failed before sending",
                        org.abai_id,
                        sheet_date,
                    )
                    await self._record(
                        DetectorDailySheetDeliveryRepository(session),
                        org,
                        sheet_date,
                        status=DELIVERY_STATUS_FAILED,
                        recipients=recipients,
                        sheets=[],
                        error=repr(exc)[:_ERROR_MAX_CHARS],
                    )
        logger.info(
            "Daily sheet mail for %s: sent=%s skipped=%s failed=%s",
            sheet_date,
            stats.sent,
            stats.skipped,
            stats.failed,
        )
        return stats

    async def _send_one(  # noqa: PLR0913
        self,
        session: AsyncSession,
        storage: AiobotoFileStorage,
        mailer: SmtpMailer | None,
        stats: MailStats,
        *,
        org: NgduRef,
        sheet_date: date,
        recipients: list[str],
    ) -> None:
        delivery_repo = DetectorDailySheetDeliveryRepository(session)
        existing = await delivery_repo.get(
            abai_ngdu_id=org.abai_id,
            sheet_date=sheet_date,
        )
        if (
            existing is not None
            and existing.status == DELIVERY_STATUS_SENT
            and not self.resend
        ):
            stats.skipped += 1
            logger.info(
                "Daily sheet mail ngdu=%s date=%s already sent at %s — skipped",
                org.abai_id,
                sheet_date,
                existing.sent_at,
            )
            return
        if not recipients:
            stats.skipped += 1
            logger.warning(
                "Daily sheet mail ngdu=%s date=%s: no recipients configured — skipped",
                org.abai_id,
                sheet_date,
            )
            await self._record(
                delivery_repo,
                org,
                sheet_date,
                status=DELIVERY_STATUS_SKIPPED,
                recipients=[],
                sheets=[],
                error="no recipients",
            )
            return

        letter = LetterInput(
            sheet_date=sheet_date,
            ngdu_name=display_ngdu_name(org.name_ru),
        )
        sheets_log: list[dict] = []
        use_case = GetDailySheetUseCase(session, storage=storage)
        files = FileService(session, storage)
        for detector_code in SHEET_DETECTOR_CODES:
            if not sheet_applies(detector_code, org.abai_id):
                continue
            query = GetDailySheetQuery(
                detector_code=detector_code,
                ngdu_id=org.id,
                sheet_date=sheet_date,
            )
            try:
                sheet = await use_case.execute(query)
                try:
                    db_file, payload = await files.download(sheet.file_id)
                except (FileMissingError, FileNotExistError):
                    # Строка ведомости есть, а объекта в S3 нет (перенос бакета,
                    # ручная чистка) — пересобрать один раз.
                    logger.warning(
                        "Daily sheet %s ngdu=%s date=%s: file_id=%s missing, rebuild",
                        detector_code,
                        org.abai_id,
                        sheet_date,
                        sheet.file_id,
                    )
                    sheet = await use_case.execute(
                        query.model_copy(update={"rebuild": True}),
                    )
                    db_file, payload = await files.download(sheet.file_id)
            except DailySheetDataNotReadyError as exc:
                await session.rollback()
                coverage = exc.details.get("coverage") or {}
                by_cits = coverage.get("source") == "cits"
                reason = (
                    f"за сутки нет {'замеров ЦИТС' if by_cits else 'телеметрии СДМО'} "
                    f"({coverage.get('stations_reporting', 0)} из "
                    f"{coverage.get('stations_total', 0)} "
                    f"{'скважин' if by_cits else 'станций'})"
                )
                letter.missing.append(MissingSheet(detector_code, reason))
                sheets_log.append({"detector_code": detector_code, "reason": reason})
                continue
            filename = attachment_filename(db_file.file)
            letter.attachments.append(
                SheetAttachment(
                    sheet=sheet,
                    filename=filename,
                    payload=payload.getvalue(),
                ),
            )
            sheets_log.append(
                {
                    "detector_code": detector_code,
                    "file_id": sheet.file_id,
                    "rows_count": sheet.rows_count,
                    "filename": filename,
                },
            )

        message = build_message(
            letter,
            recipients=recipients,
            sender=settings.SMTP_FROM,
        )
        if mailer is None:
            logger.info(
                "Daily sheet mail (dry run) ngdu=%s date=%s to=%s subject=%r "
                "attachments=%s missing=%s",
                org.abai_id,
                sheet_date,
                recipients,
                message["Subject"],
                [item.filename for item in letter.attachments],
                [item.detector_code for item in letter.missing],
            )
            stats.skipped += 1
            return
        try:
            await mailer.send(message)
        except Exception as exc:
            stats.failed += 1
            logger.exception(
                "Daily sheet mail ngdu=%s date=%s failed",
                org.abai_id,
                sheet_date,
            )
            await self._record(
                delivery_repo,
                org,
                sheet_date,
                status=DELIVERY_STATUS_FAILED,
                recipients=recipients,
                sheets=sheets_log,
                subject=str(message["Subject"]),
                error=repr(exc)[:_ERROR_MAX_CHARS],
            )
            return
        stats.sent += 1
        await self._record(
            delivery_repo,
            org,
            sheet_date,
            status=DELIVERY_STATUS_SENT,
            recipients=recipients,
            sheets=sheets_log,
            subject=str(message["Subject"]),
        )

    @staticmethod
    async def _record(  # noqa: PLR0913
        repo: DetectorDailySheetDeliveryRepository,
        org: NgduRef,
        sheet_date: date,
        *,
        status: str,
        recipients: list[str],
        sheets: list[dict],
        subject: str | None = None,
        error: str | None = None,
    ) -> None:
        await repo.upsert(
            UpsertDailySheetDeliveryDTO(
                abai_ngdu_id=org.abai_id,
                sheet_date=sheet_date,
                status=status,
                recipients=recipients,
                sheets=sheets,
                subject=subject,
                sent_at=local_now() if status == DELIVERY_STATUS_SENT else None,
                error=error,
            ),
        )
        await repo.session.commit()


@celery_app.task(name=DAILY_SHEET_MAIL_TASK)
def mail_daily_sheets(sheet_date: str | None = None, *, resend: bool = False) -> None:
    run_async(
        MailDailySheets(
            sheet_date=date.fromisoformat(sheet_date) if sheet_date else None,
            resend=resend,
        ).run(),
    )


async def main(args: argparse.Namespace) -> None:
    await MailDailySheets(
        sheet_date=args.date,
        ngdu_id=args.ngdu_id,
        resend=args.resend,
        dry_run=args.dry_run,
    ).run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Mail R2/R9 daily sheets.")
    parser.add_argument(
        "--date",
        type=date.fromisoformat,
        help="YYYY-MM-DD, по умолчанию вчера",
    )
    parser.add_argument(
        "--ngdu-id",
        type=int,
        help="org.id НГДУ; по умолчанию все подключённые",
    )
    parser.add_argument(
        "--resend",
        action="store_true",
        help="Отправить повторно, даже если за дату уже отправлено",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Собрать письмо и залогировать, не отправляя",
    )
    asyncio.run(main(parser.parse_args()))
