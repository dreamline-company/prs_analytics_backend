"""Общие таблицы подсистемы детекции нарушений.

Хранятся только события (эпизоды) и позиции чтения потоков — журнал прогонов
и несработки не пишутся. Один эпизод = одна строка ``detectors_incident``:
от выхода сигнала за порог до нормализации.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Статусы эпизода. Переход один и необратимый: active -> normalized.
INCIDENT_STATUS_ACTIVE = "active"
INCIDENT_STATUS_NORMALIZED = "normalized"

# Уровень эпизода. Только повышается: warning -> alarm (фиксирует максимум).
INCIDENT_LEVEL_WARNING = "warning"
INCIDENT_LEVEL_ALARM = "alarm"

# Причины закрытия эпизода.
CLOSE_REASON_RECOVERED = "recovered"  # метрика вернулась в норму (гистерезис)
CLOSE_REASON_STALE = "stale"  # нет данных слишком долго (пока не автоматизировано)
CLOSE_REASON_MANUAL = "manual"  # закрыт руками


class Detector(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Реестр правил детекции. Диспетчер раздаёт работу по нему."""

    __tablename__ = "detectors_detector"

    code: Mapped[str] = mapped_column(String(20), unique=True, nullable=False)
    name_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    # Источник телеметрии, на который подписано правило: sdmo / wincc / kbrs.
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    # Дебаунс диспетчера: не запускать правило чаще, чем раз в столько секунд.
    # 0 — бежать на каждый инкремент данных.
    min_interval_sec: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class DetectorIncident(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Эпизод одного сигнала на скважине: от срабатывания до нормализации.

    Физическое событие может породить несколько эпизодов (по одному на сигнал:
    момент, скорость, заполнение...) — у каждого свой таймлайн. Агрегация
    «состояние скважины» делается на чтении (worst level активных эпизодов).
    """

    __tablename__ = "detectors_incident"
    __table_args__ = (
        # Инвариант схемы: не больше одного активного эпизода на тройку
        # (правило, скважина, сигнал). Повторные детекции той же аномалии
        # через ON CONFLICT сводятся к UPDATE — дубли невозможны на уровне БД.
        Index(
            "uq_detectors_incident_active",
            "detector_code",
            "well_id",
            "reason_code",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
        Index("ix_detectors_incident_well_id_opened_at", "well_id", "opened_at"),
        Index("ix_detectors_incident_opened_at", "opened_at"),
    )

    detector_code: Mapped[str] = mapped_column(
        ForeignKey("detectors_detector.code"),
        nullable=False,
    )
    well_id: Mapped[int] = mapped_column(ForeignKey("wells_well.id"), nullable=False)
    # Сущность источника, по чьей ленте посчитан эпизод (для SDMO — локальный
    # telemetry_sdmo_station.id). Имя нейтральное: у WinCC/КБРС здесь будет их
    # идентификатор.
    entity_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    # Код сигнала внутри правила: что именно пошло не так (rod_break,
    # speed_drop, ...). Расшифровки — константы правила.
    reason_code: Mapped[str] = mapped_column(String(30), nullable=False)

    level: Mapped[str] = mapped_column(String(10), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=INCIDENT_STATUS_ACTIVE,
    )

    # Физическое начало эпизода (первая корзина серии за порогом).
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Когда эпизод увидела система; detected_at - opened_at = лаг детекции.
    detected_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Правая граница последней корзины, где аномалия подтверждалась.
    last_seen_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Когда warning перерос в alarm; NULL — не перерос (пока или вообще).
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Когда подтвердилось восстановление (гистерезис закрытия).
    normalized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(20), nullable=True)

    # Версия порогов правила на момент открытия — для воспроизводимости.
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)
    # Специфика правила: база, пороги, корзины-улики и т.п. В колонки выносится
    # только то, по чему нужно фильтровать списки.
    payload: Mapped[dict | None] = mapped_column(JSONB, nullable=True)


class DetectorCursor(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Докуда правило разобрало ленту телеметрии одной сущности источника.

    Гарантия «без потерь»: обработка привязана к позиции в данных, а не к
    расписанию. Сдвигается в одной транзакции с записью инцидентов, поэтому
    падение отбрасывает и позицию, и результаты вместе (повторная обработка
    идемпотентна благодаря частичному уникальному индексу инцидентов).
    """

    __tablename__ = "detectors_cursor"
    __table_args__ = (
        UniqueConstraint(
            "detector_code",
            "entity_id",
            name="uq_detectors_cursor_detector_code_entity_id",
        ),
    )

    detector_code: Mapped[str] = mapped_column(
        ForeignKey("detectors_detector.code"),
        nullable=False,
    )
    entity_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Правый край обработанного: конец последней оценённой корзины.
    last_event_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Когда правило последний раз бегало по сущности — дебаунс и подметальщик.
    last_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
