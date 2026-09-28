from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class WellStatusType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Справочник статусов скважин (копия emg.well_status_type)."""

    __tablename__ = "wells_well_status_type"

    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    # Мнемокод: WRK — в работе, DWN — в простое, PEXP — периодическая
    # эксплуатация и т.д. Правила опираются на него, а не на название.
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    tbd_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class WellStatusReason(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Справочник причин (копия emg.reason): ПРС, КРС, Нет подачи, ..."""

    __tablename__ = "wells_well_status_reason"

    reason_type: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    # ABAI id родительской причины, без FK: иерархию никто не читает.
    parent: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)


class WellStatus(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Интервалы статусов скважин (копия emg.well_status).

    Время хранится как в источнике — UTC, открытый интервал до 3333-12-31.
    Источник закрывает интервал правкой dend у существующей строки, поэтому
    инкрементальная загрузка помимо новых id перечитывает открытые интервалы.
    """

    __tablename__ = "wells_well_status"
    __table_args__ = (
        Index("ix_wells_well_status_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    status: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well_status_type.abai_id"),
        nullable=False,
    )
    reason: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_well_status_reason.abai_id"),
        nullable=True,
    )
    dbeg: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    dend: Mapped[datetime] = mapped_column(DateTime, index=True, nullable=False)
