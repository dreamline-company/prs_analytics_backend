from datetime import date

from sqlalchemy import (
    JSON,
    BigInteger,
    Date,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class RepairSummary(AppBaseModel, IntPkMixin):  # Сводка ПРС
    __tablename__ = "repairs_repair_reports"
    __table_args__ = (
        UniqueConstraint(
            "well_id",
            "date",
            name="uq_repairs_repair_reports_well_id_date",
        ),
    )

    repair_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id"),
        nullable=True,
    )
    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    second_well_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=True,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    brigade_number: Mapped[int] = mapped_column(Integer, nullable=False)
    pump_type: Mapped[str] = mapped_column(String(255), nullable=False)
    shift_type_number: Mapped[int] = mapped_column(Integer, nullable=False)
    car: Mapped[str] = mapped_column(String(255), nullable=False)
    device_number: Mapped[str] = mapped_column(String(255), nullable=False)
    shift_details: Mapped[list[str]] = mapped_column(JSON, nullable=False)
