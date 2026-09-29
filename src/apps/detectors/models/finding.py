"""Суточный срез правила: состояния скважин, которые не являются эпизодами.

Эпизод (``detectors_incident``) — про скважину: сигнал отказа с началом и
концом. Находка — про данные на дату: «замер устарел, запросить замер»,
дефект замера, сервисный перечень для сопровождения. Она не открывается и не
закрывается, а пересчитывается целиком за каждую дату фиксации.
"""

from datetime import date

from sqlalchemy import (
    Date,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class DetectorFinding(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "detectors_finding"
    __table_args__ = (
        UniqueConstraint(
            "detector_code",
            "fix_date",
            "well_id",
            "kind",
            name="uq_detectors_finding_detector_code_fix_date_well_id_kind",
        ),
        Index("ix_detectors_finding_fix_date", "fix_date"),
    )

    detector_code: Mapped[str] = mapped_column(
        ForeignKey("detectors_detector.code"),
        nullable=False,
    )
    # Сутки, на конец которых оценено состояние.
    fix_date: Mapped[date] = mapped_column(Date, nullable=False)
    well_id: Mapped[int] = mapped_column(ForeignKey("wells_well.id"), nullable=False)
    # Машинный код находки (stale, null_liquid, chronic_low, ...) — константы
    # правила; по нему фильтруют разделы.
    kind: Mapped[str] = mapped_column(String(30), nullable=False)
    # Формулировка для людей, как в выгрузке автора правила.
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
