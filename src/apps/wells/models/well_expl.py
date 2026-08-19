from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class WellExplType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Справочник способов эксплуатации (копия emg.well_expl_type)."""

    __tablename__ = "wells_well_expl_type"

    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    tbd_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)


class WellExpl(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Периоды способа эксплуатации скважины (копия emg.well_expl).

    Источник закрывает период правкой dend у существующей строки, поэтому
    инкрементальная загрузка помимо новых id перечитывает открытые интервалы.
    """

    __tablename__ = "wells_well_expl"
    __table_args__ = (
        Index("ix_wells_well_expl_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    expl: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_well_expl_type.abai_id"),
        nullable=True,
    )
    dbeg: Mapped[date | None] = mapped_column(Date, nullable=True)
    dend: Mapped[date | None] = mapped_column(Date, index=True, nullable=True)
