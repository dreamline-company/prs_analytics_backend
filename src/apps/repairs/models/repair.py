from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class RepairType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "repairs_repair_type"

    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    name_ru_short: Mapped[str | None] = mapped_column(Text)


class Repair(AppBaseModel, IntPkMixin, AbaiIdMixin):
    __tablename__ = "repairs_repair"

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=True,
    )
    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
    )
    work_list: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )  # Описание проделанных работ
    work_plan: Mapped[str] = mapped_column(
        Text,
        nullable=True,
    )  # Список планируемых работ
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
        nullable=True,
    )
