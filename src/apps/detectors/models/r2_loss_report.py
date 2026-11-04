"""Суточный отчёт «Обрыв штанг» (R2): скважины с сигналом станции без ремонта
и недобор нефти по ним.

Как и ведомость, это сохраняемый артефакт: строки за дату собираются один раз
по записанным эпизодам, xlsx лежит в S3, повторный запрос отдаёт готовое. Отчёт
один на дату по всем НГДУ. Письмо с ним тоже одно на дату — его судьба
хранится здесь же, чтобы утренний таск не слал отчёт повторно.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

R2_LOSS_REPORT_STATUS_COMPLETED = "completed"
R2_LOSS_REPORT_STATUS_FAILED = "failed"

R2_LOSS_MAIL_STATUS_SENT = "sent"
R2_LOSS_MAIL_STATUS_FAILED = "failed"


class DetectorR2LossReport(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "detectors_r2_loss_report"

    report_date: Mapped[date] = mapped_column(Date, unique=True, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    # Готовый xlsx в едином бакете приложения; NULL у неудачной сборки.
    file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    wells_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_oil_loss: Mapped[float] = mapped_column(Float, nullable=False, default=0)
    # Строки отчёта (R2LossRowDTO) — для API и разбора без S3.
    content: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    config_version: Mapped[str] = mapped_column(String(20), nullable=False)

    # Рассылка: одно письмо на дату.
    mail_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mail_recipients: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    mail_sent_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    mail_error: Mapped[str | None] = mapped_column(Text, nullable=True)
