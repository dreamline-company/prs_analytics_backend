"""Утренний отчёт «Обрыв штанг» (R2) за вчерашние сутки и его рассылка.

    python -m apps.detectors.tasks.r2_loss_report.r2_loss_report
    python -m apps.detectors.tasks.r2_loss_report.r2_loss_report \\
        --date 2026-10-27 [--rebuild] [--resend] [--no-mail] [--dry-run]

Celery ``detectors.r2_loss_report.build`` в 07:35 местного — после суточной
ведомости (07:30) и ночных загрузок ABAI. Отчёт берётся тем же use case, что и
API: готовый — из кэша, отсутствующий — собирается. Письмо одно на дату тем
же получателям, что и ведомости; отправленное повторно не уходит (поля
``mail_*`` строки отчёта), повтор — только ``--resend``. Без SMTP_HOST письмо
не отправляется, а логируется (dry run).
"""

import argparse
import asyncio
from datetime import date, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.celery_app import celery_app, run_async
from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO
from apps.detectors.dto.internal.repositories.r2_loss_report import (
    UpdateR2LossReportMailDTO,
)
from apps.detectors.models.r2_loss_report import (
    R2_LOSS_MAIL_STATUS_FAILED,
    R2_LOSS_MAIL_STATUS_SENT,
)
from apps.detectors.repositories.r2_loss_report import DetectorR2LossReportRepository
from apps.detectors.services.daily_sheet.mail import (
    parse_recipients,
    parse_recipients_by_ngdu,
)
from apps.detectors.services.r2_loss_report.builder import local_now
from apps.detectors.services.r2_loss_report.errors import (
    R2LossReportDataNotReadyError,
)
from apps.detectors.services.r2_loss_report.mail import (
    all_recipients,
    build_message,
)
from apps.detectors.use_cases.get_r2_loss_report import GetR2LossReportUseCase
from apps.files.errors import FileMissingError
from apps.files.services.file import FileService
from apps.models_registry import *  # noqa: F403
from core import get_logger
from core.settings import get_settings
from shared.database.s3.storage import AiobotoFileStorage
from shared.database.sql.setup import session_makers
from shared.dependencies.db import (
    get_aioboto_client_factory,
    get_aioboto_presign_client_factory,
)
from shared.integrations.mail import SmtpMailer, smtp_config_from_settings

logger = get_logger(__name__)
settings = get_settings()

R2_LOSS_REPORT_TASK = "detectors.r2_loss_report.build"
_ERROR_MAX_CHARS = 1000


class R2LossReportJob:
    def __init__(
        self,
        *,
        report_date: date | None = None,
        rebuild: bool = False,
        mail: bool = True,
        resend: bool = False,
        dry_run: bool = False,
    ) -> None:
        self.report_date = report_date or (local_now().date() - timedelta(days=1))
        self.rebuild = rebuild
        self.mail = mail
        self.resend = resend
        self.dry_run = dry_run
        self.storage = AiobotoFileStorage(
            bucket_name=settings.S3_BUCKET_NAME,
            client_factory=get_aioboto_client_factory(),
            presign_client_factory=get_aioboto_presign_client_factory(),
        )

    async def run(self) -> R2LossReportDTO | None:
        async with session_makers["app"]() as session:
            try:
                report = await GetR2LossReportUseCase(
                    session,
                    storage=self.storage,
                ).execute(self.report_date, rebuild=self.rebuild)
            except R2LossReportDataNotReadyError:
                logger.warning(
                    "R2 loss report %s: no R2 station telemetry — not built",
                    self.report_date,
                )
                return None
            if self.mail:
                await self._send(session, report)
            return report

    async def _send(self, session: AsyncSession, report: R2LossReportDTO) -> None:
        repo = DetectorR2LossReportRepository(session)
        stored = await repo.get_by_date(report.report_date)
        if (
            stored is not None
            and stored.mail_status == R2_LOSS_MAIL_STATUS_SENT
            and not self.resend
        ):
            logger.info("R2 loss report %s: already mailed", report.report_date)
            return
        recipients = all_recipients(
            parse_recipients(settings.DAILY_SHEET_MAIL_TO),
            parse_recipients_by_ngdu(settings.DAILY_SHEET_MAIL_TO_BY_NGDU),
        )
        if not recipients or report.file_id is None:
            logger.warning(
                "R2 loss report %s: nothing to mail (recipients=%s, file_id=%s)",
                report.report_date,
                len(recipients),
                report.file_id,
            )
            return

        try:
            _, payload = await FileService(session, self.storage).download(
                report.file_id,
            )
        except FileMissingError:
            # Объекта нет в бакете — пересобрать и взять новый файл.
            logger.warning(
                "R2 loss report %s: file %s missing in S3, rebuilding",
                report.report_date,
                report.file_id,
            )
            report = await GetR2LossReportUseCase(
                session,
                storage=self.storage,
            ).execute(report.report_date, rebuild=True)
            _, payload = await FileService(session, self.storage).download(
                report.file_id,
            )
        message = build_message(
            report,
            payload=payload.getvalue(),
            recipients=recipients,
            sender=settings.SMTP_FROM,
        )
        smtp = smtp_config_from_settings(settings)
        if smtp is None and not self.dry_run:
            logger.warning(
                "R2 loss report mail: SMTP_HOST/SMTP_FROM not set — running as dry run",
            )
        if smtp is None or self.dry_run:
            logger.info(
                "R2 loss report mail (dry run) %s to=%s subject=%r",
                report.report_date,
                recipients,
                message["Subject"],
            )
            return
        try:
            await SmtpMailer(smtp).send(message)
        except Exception as exc:
            logger.exception("R2 loss report %s: mail failed", report.report_date)
            await repo.update_mail(
                report.report_date,
                UpdateR2LossReportMailDTO(
                    mail_status=R2_LOSS_MAIL_STATUS_FAILED,
                    mail_recipients=recipients,
                    mail_error=repr(exc)[:_ERROR_MAX_CHARS],
                ),
            )
        else:
            await repo.update_mail(
                report.report_date,
                UpdateR2LossReportMailDTO(
                    mail_status=R2_LOSS_MAIL_STATUS_SENT,
                    mail_recipients=recipients,
                    mail_sent_at=local_now(),
                ),
            )
            logger.info(
                "R2 loss report %s mailed to %s recipients",
                report.report_date,
                len(recipients),
            )
        await session.commit()


@celery_app.task(name=R2_LOSS_REPORT_TASK)
def build_r2_loss_report(report_date: str | None = None) -> None:
    run_async(
        R2LossReportJob(
            report_date=date.fromisoformat(report_date) if report_date else None,
        ).run(),
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Отчёт «Обрыв штанг» (R2)")
    parser.add_argument("--date", type=date.fromisoformat, default=None)
    parser.add_argument("--rebuild", action="store_true")
    parser.add_argument("--no-mail", action="store_true")
    parser.add_argument("--resend", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Не отправлять письмо")
    return parser.parse_args()


async def main(args: argparse.Namespace) -> None:
    report = await R2LossReportJob(
        report_date=args.date,
        rebuild=args.rebuild,
        mail=not args.no_mail,
        resend=args.resend,
        dry_run=args.dry_run,
    ).run()
    if report is None:
        print("Нет телеметрии станций R2 за дату — отчёт не собран")  # noqa: T201
        return
    print(  # noqa: T201
        f"{report.report_date}: скважин {report.wells_count}, "
        f"недобор {report.total_oil_loss} т/сут, file_id={report.file_id}",
    )
    for row in report.rows:
        print(  # noqa: T201
            f"  {row.well_name:10} {row.oil_loss!s:>5}  {row.idle_days:>3} сут  "
            f"{row.abai_status}",
        )


if __name__ == "__main__":
    asyncio.run(main(_parse_args()))
