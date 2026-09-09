from datetime import datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

AI_STATUS_PENDING = "pending"
AI_STATUS_COMPLETED = "completed"
AI_STATUS_FAILED = "failed"


class RepairAnalytics(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "repairs_repair_analytics"

    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
        unique=True,
        nullable=False,
    )
    summary_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_reports.id"),
    )
    repair_docs_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_docs.id"),
    )
    is_finalized: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        server_default=text("false"),
        nullable=False,
    )


class RepairAnalyticsDynamogram(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "repairs_repair_analytics_dynamogram"

    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
    )
    dynamogram_before_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_dynamogram.id"),
    )
    dynamogram_after_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_dynamogram.id"),
    )


class RepairAnalyticsSPO(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "repairs_repair_analytics_spo"

    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
    )
    spo_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("repairs_spo.id"))


class RepairAnalyticsBrigadeErrorScreen(
    AppBaseModel,
    IntPkMixin,
    TimedMixinModel,
):
    __tablename__ = "repairs_repair_analytics_brigade_error_screen"

    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
    )
    cm_screen_id: Mapped[int]


class _AIResultMixin:
    """Common fields for stored LLM outputs.

    ``status`` is a plain string (``pending``/``completed``/``failed``) so new
    states can be added without a DB enum migration. ``result`` is a free-form
    JSON blob so the prompt author can change the output schema without
    touching the DB. ``prompt_version`` lets us re-run rows when the prompt
    evolves.
    """

    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text(f"'{AI_STATUS_PENDING}'"),
    )
    model_name: Mapped[str | None] = mapped_column(String(128), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class RepairDynamogramAIResult(
    AppBaseModel,
    IntPkMixin,
    TimedMixinModel,
    _AIResultMixin,
):
    """AI analysis of a single dynamogram (before or after repair)."""

    __tablename__ = "repairs_dynamogram_ai_result"

    dynamogram_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_dynamogram.id"),
        unique=True,
        nullable=False,
    )


class RepairSPOAIResult(
    AppBaseModel,
    IntPkMixin,
    TimedMixinModel,
    _AIResultMixin,
):
    """AI analysis of a single SPO measurement."""

    __tablename__ = "repairs_spo_ai_result"

    spo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_spo.id"),
        unique=True,
        nullable=False,
    )


class RepairAIAnalysis(
    AppBaseModel,
    IntPkMixin,
    TimedMixinModel,
    _AIResultMixin,
):
    """Combined AI analysis for the whole repair (aggregates per-item results)."""

    __tablename__ = "repairs_repair_ai_analysis"

    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
        nullable=False,
    )
    # Отпечаток входов (динамограммы, СПО, ПОР/акт), по которым посчитан
    # вердикт: изменился — вердикт пересчитывается.
    inputs_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)


class RepairKPI(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Computed KPI metrics for one repair (KPI ПРС).

    Deterministic aggregation over the analytics collected for the repair
    (dynamogram/SPO AI results, overall verdict, brigade violations). Values
    live in a free-form ``metrics`` JSON blob so the KPI formula owner can add
    or refine metrics without a schema migration — same convention the AI
    result tables use for ``result``.
    """

    __tablename__ = "repairs_repair_kpi"

    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default=text(f"'{AI_STATUS_PENDING}'"),
    )
    metrics: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
