from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class Telemetry(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "wells_well"

    well_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    qv_liquid: Mapped[float] = mapped_column(Float, nullable=False)
    qm_oil: Mapped[float] = mapped_column(Float, nullable=False)
