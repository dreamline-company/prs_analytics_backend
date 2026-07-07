from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class RepairBrigade(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "repairs_repair_brigade"

    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
        nullable=False,
    )
    brigade_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("org_unique_brigade.id"),
        nullable=False,
    )
