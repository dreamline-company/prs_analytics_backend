"""Отчёт «Обрыв штанг» за дату: сохранённый или собранный сейчас.

Сборка без LLM укладывается в секунды, поэтому отсутствующий отчёт собирается
прямо в запросе и сохраняется; следующий запрос отдаёт готовое.
"""

from datetime import date, datetime, time, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from apps.detectors.dto.internal.r2_loss_report import R2LossReportDTO, R2LossRowDTO
from apps.detectors.models.r2_loss_report import (
    R2_LOSS_REPORT_STATUS_COMPLETED,
    DetectorR2LossReport,
)
from apps.detectors.repositories.r2_loss_report import DetectorR2LossReportRepository
from apps.detectors.services.r2_loss_report.builder import R2LossReportBuilder
from apps.detectors.services.r2_loss_report.config import (
    CONFIG_VERSION,
    READY_AFTER_HOURS,
)
from apps.detectors.services.r2_loss_report.render import file_name
from apps.files.services.file import FileService
from shared.database.s3.storage import AiobotoFileStorage


class GetR2LossReportUseCase:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: AiobotoFileStorage,
    ) -> None:
        self.session = session
        self.storage = storage

    async def execute(
        self,
        report_date: date,
        *,
        rebuild: bool = False,
        expires_in: int = 3600,
    ) -> R2LossReportDTO:
        stored = (
            None
            if rebuild
            else await DetectorR2LossReportRepository(self.session).get_by_date(
                report_date,
            )
        )
        if stored is not None and self.is_reusable(stored):
            report = self._from_stored(stored)
        else:
            report = await R2LossReportBuilder(
                self.session,
                storage=self.storage,
            ).build(
                report_date,
            )

        if report.file_id is not None:
            info = await FileService(self.session, self.storage).get_download_info(
                report.file_id,
                expires_in=expires_in,
            )
            report.download_url, report.expires_at = info.download_url, info.expires_at
        return report

    @staticmethod
    def is_reusable(stored: DetectorR2LossReport) -> bool:
        """Готовый окончательный отчёт текущей версии правил.

        Собранный до ``READY_AFTER_HOURS`` следующего дня (за сегодня или до
        ночных загрузок ремонтов и статусов) отдаётся, но пересобирается при
        следующем запросе и перед утренней рассылкой.
        """
        ready_from = datetime.combine(
            stored.report_date + timedelta(days=1),
            time(hour=READY_AFTER_HOURS),
        )
        return (
            stored.status == R2_LOSS_REPORT_STATUS_COMPLETED
            and stored.file_id is not None
            and stored.config_version == CONFIG_VERSION
            and stored.built_at is not None
            and stored.built_at >= ready_from
        )

    @staticmethod
    def _from_stored(stored: DetectorR2LossReport) -> R2LossReportDTO:
        return R2LossReportDTO(
            report_date=stored.report_date,
            built_at=stored.built_at,
            config_version=stored.config_version,
            wells_count=stored.wells_count,
            total_oil_loss=stored.total_oil_loss,
            rows=[R2LossRowDTO.model_validate(row) for row in stored.content or []],
            file_id=stored.file_id,
            file_name=file_name(stored.report_date),
            rebuilt=False,
        )
