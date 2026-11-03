"""Технологические объекты ABAI и привязки к ним скважин (копия emg.tech*).

Отсюда берётся, на какую ГЗУ и какой отвод заведена скважина. Источник не
закрывает привязку при ликвидации или консервации скважины, поэтому состав
ГЗУ имеет смысл только вместе с текущим статусом скважины. Открытый период в
источнике — ``dend`` в далёком будущем (3333-12-31 у привязок, 9999-12-31 у
имён), не NULL.
"""

from datetime import date

from sqlalchemy import BigInteger, Date, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class TechType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Тип объекта: ГЗУ (code GMS), АГЗУ (AGMS), ЗУ (MS), ГУ, КНС, ВРП…"""

    __tablename__ = "wells_tech_type"

    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)


class Tech(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Технологический объект. Имя и родитель — в ``wells_tech_history``."""

    __tablename__ = "wells_tech"

    abai_tech_type_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_tech_type.abai_id"),
        index=True,
        nullable=True,
    )


class TechHistory(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Имя и родитель объекта по периодам.

    ``abai_parent_id`` — ABAI id родительского объекта без FK: в источнике
    ссылка может вести на объект, которого в нём уже нет.
    """

    __tablename__ = "wells_tech_history"
    __table_args__ = (
        Index("ix_wells_tech_history_abai_tech_id_dbeg", "abai_tech_id", "dbeg"),
    )

    abai_tech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_tech.abai_id"),
        nullable=False,
    )
    abai_parent_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    dbeg: Mapped[date | None] = mapped_column(Date, nullable=True)
    dend: Mapped[date | None] = mapped_column(Date, nullable=True)


class WellTech(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Привязка скважины к объекту по периодам; ``tap`` — номер отвода ГЗУ."""

    __tablename__ = "wells_well_tech"
    __table_args__ = (
        Index("ix_wells_well_tech_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    abai_tech_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_tech.abai_id"),
        index=True,
        nullable=False,
    )
    tap: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dbeg: Mapped[date | None] = mapped_column(Date, nullable=True)
    dend: Mapped[date | None] = mapped_column(Date, nullable=True)
