"""ИИ-заключения по эпизодам детекции и оценки технологов.

Заключение привязано к эпизоду и его уровню на момент генерации: максимум два
заключения на эпизод (warning и alarm), обновление улик внутри уровня новое
заключение не порождает — интерпретация меняется вместе с уровнем, а не с
каждой корзиной телеметрии. Актуальные цифры фронт берёт из payload эпизода.
"""

from sqlalchemy import (
    BigInteger,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Статусы заключения. pending пишется до похода в LLM: строка резервирует
# (incident_id, level) и не даёт параллельной задаче генерить дубль.
CONCLUSION_STATUS_PENDING = "pending"
CONCLUSION_STATUS_COMPLETED = "completed"
CONCLUSION_STATUS_FAILED = "failed"


class DetectorConclusion(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Заключение: причина по коду, рекомендации из справочника, summary от LLM."""

    __tablename__ = "detectors_conclusion"
    __table_args__ = (
        # Fingerprint генерации: не больше одного заключения на (эпизод,
        # уровень). Повторный запуск при failed/pending обновляет строку.
        UniqueConstraint(
            "incident_id",
            "level",
            name="uq_detectors_conclusion_incident_level",
        ),
        Index("ix_detectors_conclusion_well_id", "well_id"),
    )

    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("detectors_incident.id"),
        nullable=False,
    )
    # Денормализация для выборок «заключения скважины» без join'а на эпизоды.
    well_id: Mapped[int] = mapped_column(ForeignKey("wells_well.id"), nullable=False)
    detector_code: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)
    # Уровень эпизода на момент генерации (warning/alarm) — часть fingerprint.
    level: Mapped[str] = mapped_column(String(10), nullable=False)

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=CONCLUSION_STATUS_PENDING,
    )
    # Причина — человекочитаемая строка справочника; код причины = reason_code.
    cause: Mapped[str] = mapped_column(String(255), nullable=False)
    # [{step, text, role, deadline_hours, priority}] — регламент из справочника.
    recommendations: Mapped[list] = mapped_column(JSONB, nullable=False)
    # 0..1, детерминированно из улик payload (не из LLM).
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    # Объяснение улик от LLM; NULL, пока генерация не завершилась.
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    model_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    prompt_version: Mapped[str] = mapped_column(String(20), nullable=False)


class DetectorConclusionFeedback(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Оценка заключения технологом: звёзды + обязательный комментарий."""

    __tablename__ = "detectors_conclusion_feedback"

    conclusion_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("detectors_conclusion.id"),
        nullable=False,
    )
    rating: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, nullable=False)
