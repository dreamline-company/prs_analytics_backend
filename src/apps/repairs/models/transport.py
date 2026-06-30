from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class RepairTransport(AppBaseModel, IntPkMixin):
    __tablename__ = "repairs_transport"

    repair_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("repairs_repair.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    request_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        index=True,
    )

    operation_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    operation_number: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )

    status_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    status_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    closure_status: Mapped[str | None] = mapped_column(String(255), nullable=True)

    department: Mapped[str | None] = mapped_column(String(255), nullable=True)
    position: Mapped[str | None] = mapped_column(String(255), nullable=True)

    operation_created_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    planned_end_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    actual_date: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    engine_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    mileage: Mapped[float | None] = mapped_column(Float, nullable=True)

    transport_equipment_number: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    company: Mapped[str | None] = mapped_column(String(255), nullable=True)
    division: Mapped[str | None] = mapped_column(String(255), nullable=True)
    bp1: Mapped[str | None] = mapped_column(String(255), nullable=True)

    well_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    work_type: Mapped[str | None] = mapped_column(String(255), nullable=True)

    vehicle_number: Mapped[str | None] = mapped_column(String(64), nullable=True)

    vehicle_class_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    vehicle_class_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
