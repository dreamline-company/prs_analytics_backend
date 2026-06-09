from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class RepairType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "repairs_repair_type"

    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru_short: Mapped[str] = mapped_column(Text, nullable=False)


class Repair(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "repairs_repair"

    well_id: Mapped[str] = mapped_column(
        Text,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    repair_type_id: Mapped[int] = mapped_column(
        ForeignKey("repairs_repair_type.abai_id"),
    )
    start_time: Mapped[datetime] = mapped_column(
        DateTime,
        index=True,
        nullable=False,
    )
    end_time: Mapped[datetime] = mapped_column(
        DateTime,
        index=True,
        nullable=False,
    )
