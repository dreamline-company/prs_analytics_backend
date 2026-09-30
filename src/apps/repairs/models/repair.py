from datetime import datetime

from sqlalchemy import BigInteger, ColumnElement, DateTime, ForeignKey, Text, and_
from sqlalchemy.ext.hybrid import hybrid_property
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
    # Ремонт пропал из ABAI (well_workover) — когда синхронизация это
    # заметила. Запись остаётся ради привязанной аналитики, СПО и сводок, но
    # идущим ремонтом не считается.
    abai_deleted_at: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )

    @hybrid_property
    def is_open(self) -> bool:
        """Ремонт идёт: не закрыт и не удалён в ABAI."""
        return self.end_time is None and self.abai_deleted_at is None

    @is_open.inplace.expression
    @classmethod
    def _is_open_expression(cls) -> ColumnElement[bool]:
        return and_(cls.end_time.is_(None), cls.abai_deleted_at.is_(None))
