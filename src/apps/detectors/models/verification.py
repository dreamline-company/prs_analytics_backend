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

# Отметки (с 19.11.2026, правило заказчика, RULE_VERSION r2-verify-v3).
# pending — подтверждать ещё рано (ждём ПРС, ремонт не закрыт).
VERDICT_PENDING = "pending"
VERDICT_CONFIRMED = "confirmed"  # ПРС с обрывом штанг
VERDICT_NOT_CONFIRMED = "not_confirmed"  # всё остальное — с причиной

# Отметки правил до r2-verify-v3: остаются в старых строках и журнале.
VERDICT_FALSE_ALARM = "false_alarm"
VERDICT_FAILURE_LIKELY = "failure_likely"
VERDICT_FAILURE_CONFIRMED = "failure_confirmed"
VERDICT_UNDETERMINED = "undetermined"

VERDICTS = (VERDICT_PENDING, VERDICT_CONFIRMED, VERDICT_NOT_CONFIRMED)
LEGACY_VERDICTS = (
    VERDICT_FALSE_ALARM,
    VERDICT_FAILURE_LIKELY,
    VERDICT_FAILURE_CONFIRMED,
    VERDICT_UNDETERMINED,
)

# Причины отметки.
REASON_REPAIR_ROD_BREAK = "repair_rod_break"  # ПРС, в работах обрыв штанг
REASON_MOMENT_RECOVERED = "moment_recovered"  # момент вернулся к обычному за 1–6 ч
REASON_OIL_OK_DRIVE_OK = "oil_ok_drive_ok"  # нефть в норме, привод работал
REASON_REPAIR_OTHER = "repair_other"  # первый ремонт — другого вида
REASON_DURING_REPAIR = "during_repair"  # сигнал во время открытого ремонта
REASON_NO_ROD_BREAK_REPAIR = "no_rod_break_repair"  # окно ПРС истекло
# Причины правил до r2-verify-v3.
REASON_DRIVE_STOPPED = "drive_stopped"  # оператор надолго выключил привод
REASON_ABAI_STATUS = "abai_status"  # в ABAI простой с причиной-отказом
REASON_REPAIR = "repair"  # начался ремонт
REASON_NO_STATUS = "no_status"  # СДМО не присылает статус привода
REASON_NO_MEASUREMENT = "no_measurement"  # нет замера в окне
REASON_OIL_NOT_MEASURED = "oil_not_measured"  # нефть в замерах пустая или 0
REASON_OIL_LOW = "oil_low"  # нефть есть, но меньше половины нормы
REASON_NO_OIL_NORM = "no_oil_norm"  # нет ни техрежима, ни замеров до тревоги
REASON_DRIVE_UNSTABLE = "drive_unstable"  # нефть в норме, но привод работал < порога

# «Не подтверждено» по этим причинам значит, что сигнала на деле не было
# (момент или нефть вернулись, тревога — артефакт ремонта). Остальные
# «не подтверждено» — отказ другого вида или неизвестный: скважина может и
# не добывать (отчёт недобора их не исключает).
FALSE_SIGNAL_REASONS = frozenset(
    {REASON_MOMENT_RECOVERED, REASON_OIL_OK_DRIVE_OK, REASON_DURING_REPAIR},
)

# Подписи для людей (API, выгрузки, ИИ-чат).
VERDICT_LABELS_RU = {
    VERDICT_PENDING: "Ожидает подтверждения",
    VERDICT_CONFIRMED: "Подтверждено",
    VERDICT_NOT_CONFIRMED: "Не подтверждено",
    VERDICT_FALSE_ALARM: "Тревога ложная",
    VERDICT_FAILURE_LIKELY: "Скорее всего поломка",
    VERDICT_FAILURE_CONFIRMED: "Поломка подтверждена",
    VERDICT_UNDETERMINED: "Не удалось проверить",
}
REASON_LABELS_RU = {
    REASON_REPAIR_ROD_BREAK: "ПРС с обрывом штанг",
    REASON_MOMENT_RECOVERED: "Момент восстановился",
    REASON_OIL_OK_DRIVE_OK: "Нефть в норме, привод работал",
    REASON_REPAIR_OTHER: "Ремонт другого вида",
    REASON_DURING_REPAIR: "Сигнал во время ремонта",
    REASON_NO_ROD_BREAK_REPAIR: "ПРС с обрывом штанг не было",
    REASON_DRIVE_STOPPED: "Привод стоял 10+ часов",
    REASON_ABAI_STATUS: "В ABAI поставили простой",
    REASON_REPAIR: "Начался ремонт",
    REASON_NO_STATUS: "СДМО не присылал статус привода",
    REASON_NO_MEASUREMENT: "Не было замера нефти",
    REASON_OIL_NOT_MEASURED: "Нефть в замерах пустая или 0",
    REASON_OIL_LOW: "Нефти меньше половины обычного",
    REASON_NO_OIL_NORM: "Не с чем сравнить нефть",
    REASON_DRIVE_UNSTABLE: "Нефть в норме, но привод останавливали",
}


def is_false_signal(verdict: str | None, reason: str | None) -> bool:
    """Проверка признала, что сигнала на деле не было (в т.ч. старой отметкой)."""
    if verdict == VERDICT_FALSE_ALARM:
        return True
    return verdict == VERDICT_NOT_CONFIRMED and reason in FALSE_SIGNAL_REASONS


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
