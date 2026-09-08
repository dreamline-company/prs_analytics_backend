from datetime import date

from sqlalchemy import BigInteger, Date, Float, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class TechRegime(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "telemetry_tech_regime"
    # Последний / действующий режим скважины (LATERAL по скважине).
    __table_args__ = (
        Index(
            "ix_telemetry_tech_regime_abai_well_id_start_date",
            "abai_well_id",
            "start_date",
        ),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    liquid: Mapped[float | None] = mapped_column(Float, nullable=True)
    oil: Mapped[float | None] = mapped_column(Float, nullable=True)
