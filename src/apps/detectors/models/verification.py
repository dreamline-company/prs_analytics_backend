"""Проверка эпизодов детекции независимыми данными: было ли нарушение на деле.

Правило (R2 и др.) говорит «похоже на отказ»; проверка смотрит, что стало со
скважиной после срабатывания — ремонт и статусы ABAI, работа привода по СДМО,
замеры нефти — и ставит эпизоду отметку. Одна строка на эпизод (текущая
отметка), каждая смена отметки или её причины — строка журнала.

Отметка хранится отдельно от эпизода: эпизод описывает сигнал правила (открылся,
закрылся), проверка — что было на самом деле, и таблица одна для всех правил.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Отметки. pending — окно проверки ещё не закрылось и решать рано.
VERDICT_PENDING = "pending"
VERDICT_FALSE_ALARM = "false_alarm"
VERDICT_FAILURE_LIKELY = "failure_likely"
VERDICT_FAILURE_CONFIRMED = "failure_confirmed"
VERDICT_UNDETERMINED = "undetermined"

VERDICTS = (
    VERDICT_PENDING,
    VERDICT_FALSE_ALARM,
    VERDICT_FAILURE_LIKELY,
    VERDICT_FAILURE_CONFIRMED,
    VERDICT_UNDETERMINED,
)

# Причины отметки.
REASON_OIL_OK_DRIVE_OK = "oil_ok_drive_ok"  # нефть в норме, привод работал
REASON_DRIVE_STOPPED = "drive_stopped"  # оператор надолго выключил привод
REASON_ABAI_STATUS = "abai_status"  # в ABAI простой с причиной-отказом
REASON_REPAIR = "repair"  # начался ремонт
REASON_REPAIR_ROD_BREAK = "repair_rod_break"  # ремонт, в работах «обрыв»
REASON_NO_STATUS = "no_status"  # СДМО не присылает статус привода
REASON_NO_MEASUREMENT = "no_measurement"  # нет замера в окне
REASON_OIL_NOT_MEASURED = "oil_not_measured"  # нефть в замерах пустая или 0
REASON_OIL_LOW = "oil_low"  # нефть есть, но меньше половины нормы
REASON_NO_OIL_NORM = "no_oil_norm"  # нет ни техрежима, ни замеров до тревоги
REASON_DRIVE_UNSTABLE = "drive_unstable"  # нефть в норме, но привод работал < порога


class DetectorVerification(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Текущая отметка проверки эпизода."""

    __tablename__ = "detectors_verification"
    __table_args__ = (
        UniqueConstraint("incident_id", name="uq_detectors_verification_incident"),
        Index("ix_detectors_verification_well_id", "well_id"),
        # Ежечасная проверка берёт только неокончательные отметки.
        Index(
            "ix_detectors_verification_not_final",
            "incident_id",
            postgresql_where=text("is_final = false"),
        ),
    )

    incident_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("detectors_incident.id"),
        nullable=False,
    )
    well_id: Mapped[int] = mapped_column(ForeignKey("wells_well.id"), nullable=False)
    detector_code: Mapped[str] = mapped_column(String(20), nullable=False)

    verdict: Mapped[str] = mapped_column(String(30), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(40), nullable=True)
    # Окончательная: отметка больше не меняется (подтверждённый отказ сразу,
    # остальные — по истечении срока проверки).
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Время в ДАННЫХ, на котором основана отметка: замер + часы работы привода,
    # начало ремонта, начало статуса ABAI, момент набора часов остановки.
    evidence_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Когда проверка поставила текущую отметку (настенные часы, местное время).
    decided_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    final_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    # Доказательства: oil / drive / abai / repair — только найденные блоки.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Версия порогов проверки — чтобы старые отметки объяснялись старыми порогами.
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)


class DetectorVerificationHistory(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Журнал смен отметки и её причины."""

    __tablename__ = "detectors_verification_history"
    __table_args__ = (
        Index(
            "ix_detectors_verification_history_verification_id",
            "verification_id",
        ),
    )

    verification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("detectors_verification.id"),
        nullable=False,
    )
    verdict_from: Mapped[str | None] = mapped_column(String(30), nullable=True)
    verdict_to: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_from: Mapped[str | None] = mapped_column(String(40), nullable=True)
    reason_to: Mapped[str | None] = mapped_column(String(40), nullable=True)
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Снимок доказательств на момент смены.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    changed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    rule_version: Mapped[str] = mapped_column(String(50), nullable=False)
