from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class TechRegime(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "telemetry_tech_regime"

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    liquid: Mapped[float | None] = mapped_column(Float, nullable=True)
    oil: Mapped[float | None] = mapped_column(Float, nullable=True)
