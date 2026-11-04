from datetime import date

from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from apps.detectors.dto.internal.repositories.r2_loss_report import (
    UpdateR2LossReportMailDTO,
    UpsertR2LossReportDTO,
)
from apps.detectors.models.r2_loss_report import DetectorR2LossReport
from shared.repository.sqlalchemy import AsyncAlchemyRepository, QuerySpec

# Что перезаписывается при пересборке; судьба письма при этом сохраняется.
_MUTABLE_COLUMNS: tuple[str, ...] = (
    "status",
    "file_id",
    "wells_count",
    "total_oil_loss",
    "content",
    "built_at",
    "error",
    "config_version",
)


class DetectorR2LossReportRepository(
    AsyncAlchemyRepository[
        UpsertR2LossReportDTO,
        UpdateR2LossReportMailDTO,
        DetectorR2LossReport,
    ],
):
    model = DetectorR2LossReport

    async def get_by_date(self, report_date: date) -> DetectorR2LossReport | None:
        return await self.get_one(
            QuerySpec(filters=(DetectorR2LossReport.report_date == report_date,)),
        )

    async def upsert(self, data: UpsertR2LossReportDTO) -> DetectorR2LossReport:
        """Записать отчёт за дату; пересборка перезаписывает строку.

        Старый файл не удаляется: строка ``files_file`` остаётся, отчёт ссылается
        на новый — история версий на случай разбора.
        """
        stmt = pg_insert(DetectorR2LossReport).values(**data.model_dump())
        stmt = stmt.on_conflict_do_update(
            index_elements=(DetectorR2LossReport.report_date,),
            set_={
                **{name: getattr(stmt.excluded, name) for name in _MUTABLE_COLUMNS},
                "updated_at": func.now(),
            },
        ).returning(DetectorR2LossReport)
        result = await self.session.execute(stmt)
        return result.scalar_one()

    async def update_mail(
        self,
        report_date: date,
        data: UpdateR2LossReportMailDTO,
    ) -> None:
        await self.update(
            data,
            filters=(DetectorR2LossReport.report_date == report_date,),
            partial=False,
        )
