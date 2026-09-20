"""Суточные ведомости отклонений по правилам детекции (R2 / R9) на НГДУ.

Ведомость — сохраняемый артефакт, а не расчёт на лету: строки за дату
собираются один раз, файл лежит в S3, повторный запрос отдаёт готовое.
Правило при этом не перезапускается — читаются уже записанные эпизоды, а
состояние «на дату» восстанавливается по меткам времени эпизода.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

DAILY_SHEET_STATUS_COMPLETED = "completed"
DAILY_SHEET_STATUS_FAILED = "failed"


class DetectorDailySheet(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Одна ведомость = (правило, НГДУ, дата, месторождения); пересборка
    перезаписывает строку. Ведомость по всему НГДУ и ведомости по отдельным
    месторождениям — разные артефакты с общим сборщиком."""

    __tablename__ = "detectors_daily_sheet"
    __table_args__ = (
        UniqueConstraint(
            "detector_code",
            "abai_ngdu_id",
            "sheet_date",
            "oil_field_prefixes",
            name="uq_detectors_daily_sheet_detector_ngdu_date_fields",
        ),
    )

    detector_code: Mapped[str] = mapped_column(
        ForeignKey("detectors_detector.code"),
        nullable=False,
    )
    # НГДУ-источник телеметрии (AbaiNGDUIDsEnum) — как у станций СДМО.
    abai_ngdu_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    sheet_date: Mapped[date] = mapped_column(Date, nullable=False)
    # Фильтр по месторождениям: префиксы имён скважин через запятую,
    # отсортированные («BLG,GRN»); пустая строка — весь НГДУ. Часть ключа,
    # поэтому не NULL: в UNIQUE два NULL не конфликтуют.
    oil_field_prefixes: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        default="",
        server_default="",
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)

    # Готовый docx в едином бакете приложения; NULL у неудачной сборки.
    file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    rows_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Охват суток: сколько станций НГДУ дали телеметрию и докуда дошло правило.
    # Без него пустая таблица неотличима от необработанных данных.
    coverage: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Строки, ТОП и примечания на момент сборки — для UI и аудита; файл
    # повторяет их один в один.
    content: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    built_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Версия порогов/формулировок ведомости на момент сборки.
    config_version: Mapped[str] = mapped_column(String(20), nullable=False)
