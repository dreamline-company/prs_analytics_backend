from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class RepairAnalytics(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "repairs_repair_analytics"
    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
    )
    summary_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_reports.id"),
    )
    repair_docs_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_docs.id"),
    )


class RepairAnalyticsDynamogram(AppBaseModel, IntPkMixin, TimedMixinModel):
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
):  # модель main_brigadeerrorscreen из ЦМ
    analytics_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair_analytics.id"),
        unique=True,
    )
    cm_screen_id: Mapped[int]
