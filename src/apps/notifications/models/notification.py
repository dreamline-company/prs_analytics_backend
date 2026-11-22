"""Журнал уведомлений и общая отметка «просмотрено».

``notifications_notification`` — неизменяемый журнал: строки только
дописываются. Развитие события (эскалация, закрытие, итог проверки) — новая
строка, старые не правятся и не удаляются. Повторный прогон производителя
дублей не даёт: уникальный ``dedup_key``.

``notifications_notification_view`` — «просмотрено», одно на уведомление и
общее для всех экранов (пользователей нет). Появятся пользователи — добавится
``user_id``, уникальность станет ``(notification_id, user_id)``.

Все времена — местное наивное время, проставляется в Python: БД работает в
UTC, и ``server_default=now()`` дал бы сдвиг на 5 часов.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel


class Notification(AppBaseModel):
    __tablename__ = "notifications_notification"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('info', 'warning', 'alarm')",
            name="severity",
        ),
        # Уведомления скважины, новые сверху.
        Index("ix_notifications_notification_well_id_id", "well_id", "id"),
        # Цепочка одного эпизода.
        Index(
            "ix_notifications_notification_source",
            "source_type",
            "source_id",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # Что случилось, машинно: ``<домен>.<событие>`` — r2.escalated, system.test.
    event_code: Mapped[str] = mapped_column(String(50), nullable=False)
    # info / warning / alarm: тост, автоскрытие, звук.
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    title_ru: Mapped[str] = mapped_column(String(255), nullable=False)
    body_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NULL у системных событий (сбой источника и т. п.).
    well_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=True,
    )
    # НГДУ (AbaiNGDUIDsEnum) на момент записи: у событий по скважине — её
    # НГДУ, у системных — НГДУ сбоя. Наружу отдаётся как org.id.
    abai_ngdu_id: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    # Запись-источник: эпизод детектора, ремонт, прогон…
    source_type: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dedup_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    # Структурные данные события; ``facts`` — [{label_ru, value_ru}] для карточки.
    payload: Mapped[dict] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    )
    # Когда случилось по данным.
    event_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Когда система записала; окно подстраховочного опроса хаба.
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)


class NotificationView(AppBaseModel):
    __tablename__ = "notifications_notification_view"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    notification_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("notifications_notification.id"),
        nullable=False,
        unique=True,
    )
    # Окно подстраховочного опроса хаба (кадр viewed).
    viewed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
