from sqlalchemy import BigInteger, Boolean, ForeignKey, text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


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
