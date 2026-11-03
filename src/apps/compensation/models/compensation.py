"""Контур компенсации: пул доноров и пары «потеря → донор».

Потеря — скважина, которая стоит (простой в ABAI или незавершённый ремонт);
донор — работающая скважина из пула, которую можно разогнать на ступень
скорости. Пары подбираются раз в час: пара живёт, пока стоит скважина с
потерей и работает донор, а пару «на согласовании» может перехватить потеря
крупнее (закрытие ``donor_preempted``, с 31.10.2026).
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
    """Кандидат в доноры: строка ежедневного расчёта пула по методике (ред. 3).

    До 04.11.2026 — разовая загрузка Excel автора. Строки не удаляются (на них
    ссылаются пары): скважина, не прошедшая критерии, остаётся с
    ``in_pool = false``. Прошедшие критерии без телеметрии — отдельный перечень
    методики: ``has_telemetry = false``, в пул и подбор не входят. В колонки
    вынесено то, что читают подбор и эндпоинты; метрики, причины отсева и
    пометки — в ``source_row``.
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
    # Прошёл все критерии и есть телеметрия — участвует в подборе.
    in_pool: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    has_telemetry: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default=text("true"),
    )
    # ГЗУ скважины (wells_tech.abai_id) и отвод — подбор только внутри своей ГЗУ.
    gzu_abai_id: Mapped[int | None] = mapped_column(
        BigInteger,
        index=True,
        nullable=True,
    )
    tap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


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


class CompensationLoss(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Потеря — стоящая скважина контура и её величина; пишет подбор раз в час.

    Стоит — состояние «стоит» или «ремонт» в таблице состояний скважин (ABAI +
    привод СДМО). Величина — средняя фактическая нефть по ABAI за 10 суток до
    остановки (методика, п. 8.1); нет факта — план техрежима
    (``loss_source = "plan"``); нет ни того, ни другого — ``loss`` пуст.
    Эндпоинты читают потери отсюда, чтобы видеть те же цифры, что подбор.
    """

    __tablename__ = "compensation_loss"

    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells_well.id"),
        unique=True,
        nullable=False,
    )
    abai_ngdu_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    stop_kind: Mapped[str] = mapped_column(String(10), nullable=False)  # idle / repair
    stop_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    stop_since: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    loss: Mapped[float | None] = mapped_column(Float, nullable=True)
    loss_source: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gzu_abai_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    calculated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
