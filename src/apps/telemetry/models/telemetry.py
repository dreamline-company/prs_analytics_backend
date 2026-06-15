from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Telemetry(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "telemetry_well"

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    qv_liquid: Mapped[float] = mapped_column(Float, nullable=False)
    qm_oil: Mapped[float] = mapped_column(Float, nullable=False)
