"""Категория скважины и её геообъекты из ABAI (копии emg.well_category*, emg.geo*).

Категория — нефтяная (OIL), нагнетательная (INJ), наблюдательная (OBS)… — по
периодам; у каждой скважины одна открытая. Геообъекты — почти всегда горизонты
(``wells_geo_type.code`` HRZ, ABAI id 4); у скважины их бывает несколько сразу.

Открытый период в источнике — ``dend`` в далёком будущем, не NULL: 3333-12-31
у категорий, 9999-12-31 или 3333-12-31 у горизонтов. Действующее на дату ``d``
— ``dbeg <= d < dend``. NULL в дате значит, что в источнике там мусор раньше
1900 года (до нашей эры, 1899-12-31): ``dbeg`` NULL — «начало неизвестно»,
считать с незапамятных времён (``dbeg is null or dbeg <= d``); ``dend`` NULL —
период закрыт неизвестно когда, на дату не действует. На 16.11.2026 NULL есть
только в ``dbeg`` пяти привязок горизонтов.

Привязки горизонтов копируются как есть: в источнике есть перевёрнутые периоды
(``dbeg > dend``), дубли (скважина, горизонт, ``dbeg``) и пересечения периодов
одного горизонта — на дату нужно брать уникальные ``abai_geo_id``. У геообъекта
нет родителя: горизонт не связан с месторождением, одно имя горизонта
встречается под разными id — сравнивать по ``abai_geo_id``, а не по имени.
"""

from datetime import date, datetime

from sqlalchemy import BigInteger, Date, DateTime, ForeignKey, Index, Text
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AbaiIdMixin, AppBaseModel, IntPkMixin


class WellCategoryType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Справочник категорий: OIL, INJ, SINJ, OBS, EXPL, WTR, GAS, ABS…"""

    __tablename__ = "wells_well_category_type"

    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)


class WellCategory(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Категория скважины по периодам.

    Даты — timestamp без пояса, как в источнике: у старых записей бывает
    время 01:00, 05:00, 08:00, 09:00.
    """

    __tablename__ = "wells_well_category"
    __table_args__ = (
        Index("ix_wells_well_category_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    abai_category_type_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well_category_type.abai_id"),
        index=True,
        nullable=False,
    )
    dbeg: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    dend: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class GeoType(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Тип геообъекта: HRZ горизонт, OBJ объект, FLD месторождение, GBLK блок…"""

    __tablename__ = "wells_geo_type"

    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)


class Geo(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Геообъект; ``field_code`` (префикс имени скважины) — только у месторождений."""

    __tablename__ = "wells_geo"

    abai_geo_type_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_geo_type.abai_id"),
        index=True,
        nullable=True,
    )
    name_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    field_code: Mapped[str | None] = mapped_column(Text, nullable=True)


class WellGeo(AppBaseModel, IntPkMixin, AbaiIdMixin):
    """Привязка скважины к геообъекту (почти всегда к горизонту) по периодам."""

    __tablename__ = "wells_well_geo"
    __table_args__ = (
        Index("ix_wells_well_geo_abai_well_id_dbeg", "abai_well_id", "dbeg"),
    )

    abai_well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.abai_id"),
        nullable=False,
    )
    abai_geo_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_geo.abai_id"),
        index=True,
        nullable=False,
    )
    dbeg: Mapped[date | None] = mapped_column(Date, nullable=True)
    dend: Mapped[date | None] = mapped_column(Date, nullable=True)
