from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class Telemetry(AppBaseModel, IntPkMixin):
    __tablename__ = "telemetry_well"

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    qv_liquid: Mapped[float | None] = mapped_column(Float, nullable=True)
    qm_oil: Mapped[float | None] = mapped_column(Float, nullable=True)
    ngdu_id: Mapped[int] = mapped_column(ForeignKey("org_ngdu.id"), nullable=False)
    oil_field: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gzu: Mapped[str | None] = mapped_column(String(10), nullable=True)
    otvod: Mapped[int | None] = mapped_column(String(10), nullable=True)
