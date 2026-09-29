"""Контур компенсации: пул доноров и пары «потеря → донор».

Потеря — скважина, которая стоит (простой в ABAI или незавершённый ремонт);
донор — работающая скважина из пула, которую можно разогнать на ступень
скорости. Пары подбираются раз в час и не перетасовываются: пара живёт, пока
стоит скважина с потерей и работает донор.
"""

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from apps.compensation.constants import RECOMMENDATION_PENDING
from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class CompensationDonor(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Донор пула — строка расчёта автора (сейчас разовая загрузка Excel).

    В колонки вынесено то, что читают подбор и эндпоинты; строка источника
    целиком лежит в ``source_row``.
    """

    __tablename__ = "compensation_donor"

    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells_well.id"),
        unique=True,
        nullable=False,
    )
    abai_ngdu_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # Название месторождения из пула («Вос. Молдабек»); в oil_fields только префикс.
    oil_field_name: Mapped[str] = mapped_column(String(100), nullable=False)
    gzu: Mapped[str | None] = mapped_column(String(50), nullable=True)
    lift_type: Mapped[str] = mapped_column(String(10), nullable=False)  # ШГН / ЭВН
    qn: Mapped[float] = mapped_column(Float, nullable=False)  # Qн, т/сут
    water_cut: Mapped[float | None] = mapped_column(Float, nullable=True)
    submergence_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    speed: Mapped[float] = mapped_column(Float, nullable=False)  # текущая скорость
    step_percent: Mapped[int] = mapped_column(Integer, nullable=False)
    gain: Mapped[float] = mapped_column(Float, nullable=False)  # прирост, т/сут
    speed_margin_checked: Mapped[bool] = mapped_column(Boolean, nullable=False)
    risk: Mapped[str] = mapped_column(String(10), nullable=False)
    pool_date: Mapped[date] = mapped_column(Date, nullable=False)
    source_row: Mapped[dict] = mapped_column(JSONB, nullable=False)


class CompensationRecommendation(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Пара «потеря → донор». Открыта, пока ``closed_at`` пуст."""

    __tablename__ = "compensation_recommendation"
    __table_args__ = (
        # Донор закрыт не больше чем за одной потерей одновременно.
        Index(
            "uq_compensation_recommendation_open_donor",
            "donor_id",
            unique=True,
            postgresql_where=text("closed_at IS NULL"),
        ),
        Index("ix_compensation_recommendation_loss_well_id", "loss_well_id"),
    )

    loss_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    donor_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("compensation_donor.id"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RECOMMENDATION_PENDING,
    )
    # Снимок на момент подбора: потеря, прирост, скорость, расстояние.
    loss: Mapped[float] = mapped_column(Float, nullable=False)
    gain: Mapped[float] = mapped_column(Float, nullable=False)
    speed_from: Mapped[float] = mapped_column(Float, nullable=False)
    speed_to: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    close_reason: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Согласование — эндпоинта пока нет.
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by: Mapped[str | None] = mapped_column(String(255), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
