from sqlalchemy import BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class RepairSummary(AppBaseModel, IntPkMixin):  # Сводка ПРС
    __tablename__ = "repairs_repair_reports"

    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
        nullable=False,
    )
