from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import ABAIBaseModel


class Well(ABAIBaseModel):
    __tablename__ = "well"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    project_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата создания проекта",
    )

    uwi: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Номер скважины",
    )

    rte: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Альтитуда стола ротора",
    )

    whc: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("spatial_object.id"),
        nullable=True,
        comment="Координаты устья. Ссылка на поле id таблицы spatial_object",
    )

    whc_alt: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Альтитуда устья",
    )

    whc_h: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="Превышение стола ротора",
    )

    bottom_coord: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("spatial_object.id"),
        nullable=True,
        comment="Координаты забоя. Ссылка на поле id таблицы spatial_object",
    )

    well_type: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("well_type.id"),
        nullable=True,
        comment="Тип скважины. Ссылка на поле id таблицы well_type",
    )


class SpatialObject(ABAIBaseModel):
    __tablename__ = "spatial_object"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    coord_system: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("coord_system.id"),
        nullable=True,
        comment="Система координат. Ссылка на поле id таблицы coord_system",
    )

    spatial_object_type: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Тип пространственного объекта",
    )

    coord_point: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POINT"),
        nullable=True,
        comment="Координаты объекта типа ТОЧКА",
    )

    coord_polygon: Mapped[object | None] = mapped_column(
        Geometry(geometry_type="POLYGON"),
        nullable=True,
        comment="Координаты объекта типа ПОЛИГОН",
    )


class CoordSystem(ABAIBaseModel):
    __tablename__ = "coord_system"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    mn: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Мнемоника системы координат",
    )

    name_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Наименование на русском языке",
    )

    srid: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        comment="Spatial Reference ID",
    )


class WellWorkover(ABAIBaseModel):
    __tablename__ = "well_workover"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    well: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("well.id"),
        nullable=True,
        comment="Скважина. Ссылка на поле id таблицы well",
    )

    repair_type: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Тип ремонта",
    )

    dbeg: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="Дата начала работ",
    )

    dend: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
        comment="Дата окончания работ",
    )

    contractor: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Подрядчик",
    )

    work_list: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Описание проделанных работ",
    )

    repair_work_type: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("repair_work_type.id"),
        nullable=True,
        comment="Вид ремонтных работ. Ссылка на поле id таблицы repair_work_type",
    )

    work_plan: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Список планируемых работ",
    )

    by_ourselves: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
        comment="Собственными силами",
    )

    brigade: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Бригада",
    )

    brigadier: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Бригадир",
    )


class RepairWorkType(ABAIBaseModel):
    __tablename__ = "repair_work_type"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    name_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Наименование на русском языке",
    )

    name_short_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Краткое наименование на русском языке",
    )

    unv: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="УНВ",
    )
