from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin


class SPO(AppBaseModel, IntPkMixin):
    __tablename__ = "repairs_spo"
    file_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=False,
    )
    chart_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    notes_file_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("files_file.id"),
        nullable=True,
    )
    snapshot_time: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
