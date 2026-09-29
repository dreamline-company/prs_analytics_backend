from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class Telemetry(AppBaseModel, IntPkMixin):
    __tablename__ = "telemetry_well"
    # Последний замер скважины (LATERAL по скважине) и ряды за период;
    # окно НГДУ по времени — перечитка источника загрузчиком WinCC и R10.
    __table_args__ = (
        Index("ix_telemetry_well_well_id_date_time", "well_id", "date_time"),
        Index(
            "ix_telemetry_well_abai_ngdu_id_date_time",
            "abai_ngdu_id",
            "date_time",
        ),
    )

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    date_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    qv_liquid: Mapped[float | None] = mapped_column(Float, nullable=True)
    qm_oil: Mapped[float | None] = mapped_column(Float, nullable=True)
    qv_water: Mapped[float | None] = mapped_column(Float, nullable=True)
    qm_water: Mapped[float | None] = mapped_column(Float, nullable=True)
    abai_ngdu_id: Mapped[int] = mapped_column(BigInteger, index=True, nullable=False)
    oil_field: Mapped[str | None] = mapped_column(String(10), nullable=True)
    gzu: Mapped[str | None] = mapped_column(String(10), nullable=True)
    otvod: Mapped[int | None] = mapped_column(String(10), nullable=True)
