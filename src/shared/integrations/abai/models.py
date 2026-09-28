from datetime import date, datetime

from sqlalchemy import (
    DOUBLE_PRECISION,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import UserDefinedType

from shared.database.sql.models import ABAIBaseModel


class PgPoint(UserDefinedType):
    """Родной тип PostgreSQL ``point``.

    В ABAI координаты лежат геометрическими типами Postgres, а не PostGIS:
    объявлять их как ``Geometry`` нельзя — SELECT оборачивал бы колонку в
    ``ST_AsEWKB()``, которой для типа ``point`` не существует. Драйвер отдаёт
    значение как ``asyncpg.Point``.
    """

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        _ = kw
        return "point"


class PgPolygon(UserDefinedType):
    """Родной тип PostgreSQL ``polygon`` (см. :class:`PgPoint`)."""

    cache_ok = True

    def get_col_spec(self, **kw: object) -> str:
        _ = kw
        return "polygon"


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
        PgPoint(),
        nullable=True,
        comment="Координаты объекта типа ТОЧКА",
    )

    coord_polygon: Mapped[object | None] = mapped_column(
        PgPolygon(),
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


class TechModeProdOil(ABAIBaseModel):
    __tablename__ = "tech_mode_prod_oil"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    well: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("well.id"),
        nullable=False,
        comment="Скважина. Ссылка на поле id таблицы well",
    )

    dbeg: Mapped[date] = mapped_column(
        Date,
        nullable=False,
        comment="Дата начала",
    )

    dend: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата окончания",
    )

    liquid: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
        comment="Дебит жидкости (м.куб/сут)",
    )

    oil: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
        comment="Дебит нефти (т/сут)",
    )

    wcut: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
        comment="Обводненность (%)",
    )

    oil_density: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
        comment="Плотность нефти (т/м.куб)",
    )


class Org(ABAIBaseModel):
    __tablename__ = "org"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    parent: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("org.id"),
        nullable=True,
        comment=(
            "Вышестоящий объект орг. структуры "
            "(исторические данные по полю выбирать из org_history)"
        ),
    )

    name_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Наименование на русском языке "
            "(исторические данные по полю выбирать из org_history)"
        ),
    )

    name_short_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment=(
            "Краткое наименование на русском языке "
            "(исторические данные по полю выбирать из org_history)"
        ),
    )

    org_type: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Тип объекта орг. структуры. Ссылка на поле id таблицы org_type",
    )


class Brigade(ABAIBaseModel):
    __tablename__ = "brigade"

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

    own: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )

    org: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("org.id"),
        nullable=True,
        comment="Оргструктура",
    )


class WellOrg(ABAIBaseModel):
    __tablename__ = "well_org"

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

    org: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("org.id"),
        nullable=True,
        comment="Объект орг. структуры. Ссылка на поле id таблицы org",
    )

    dbeg: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата начала действия",
    )

    dend: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата окончания действия",
    )


class WellExplType(ABAIBaseModel):
    __tablename__ = "well_expl_type"

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

    tbd_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Идентификатор в справочнике-источнике",
    )

    code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Мнемокод способа эксплуатации",
    )


class WellExpl(ABAIBaseModel):
    __tablename__ = "well_expl"

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

    expl: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("well_expl_type.id"),
        nullable=True,
        comment="Способ эксплуатации. Ссылка на поле id таблицы well_expl_type",
    )

    dbeg: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата начала действия",
    )

    dend: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
        comment="Дата окончания действия",
    )


class WellStatusType(ABAIBaseModel):
    __tablename__ = "well_status_type"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    name_ru: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Наименование на русском языке",
    )

    code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Мнемокод статуса (WRK, DWN, PEXP, ...)",
    )

    name_short_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Краткое наименование на русском языке",
    )

    tbd_id: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Идентификатор в справочнике-источнике",
    )


class Reason(ABAIBaseModel):
    __tablename__ = "reason"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    reason_type: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        comment="Тип причины",
    )

    name_ru: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Наименование на русском языке",
    )

    code: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Мнемокод причины",
    )

    parent: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
        comment="Родительская причина",
    )

    name_short_ru: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
        comment="Краткое наименование на русском языке",
    )


class WellStatus(ABAIBaseModel):
    """Интервал статуса скважины. Время — UTC, открытый интервал до 3333-12-31."""

    __tablename__ = "well_status"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        comment="ID",
    )

    well: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("well.id"),
        nullable=False,
        comment="Скважина. Ссылка на поле id таблицы well",
    )

    status: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("well_status_type.id"),
        nullable=False,
        comment="Статус. Ссылка на поле id таблицы well_status_type",
    )

    dbeg: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="Начало действия (UTC)",
    )

    dend: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        comment="Окончание действия (UTC); 3333-12-31 — не закрыт",
    )

    reason: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("reason.id"),
        nullable=True,
        comment="Причина. Ссылка на поле id таблицы reason",
    )


class Metric(ABAIBaseModel):
    """Справочник метрик ГДИС (emg_integration.metric через FDW)."""

    __tablename__ = "metric"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    name_ru: Mapped[str] = mapped_column(Text, nullable=False)
    data_type: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    parent: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    name_short_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    code: Mapped[str | None] = mapped_column(Text, nullable=True)
    dict_table: Mapped[str | None] = mapped_column(Text, nullable=True)
    value_double_min: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )
    value_double_max: Mapped[float | None] = mapped_column(
        DOUBLE_PRECISION,
        nullable=True,
    )


class GdisCurrent(ABAIBaseModel):
    """Исследование скважины (emg_integration.gdis_current через FDW)."""

    __tablename__ = "gdis_current"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    well: Mapped[int] = mapped_column(BigInteger, nullable=False)
    meas_date: Mapped[date] = mapped_column(Date, nullable=False)
    reason: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    device: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    transcript_dynamogram: Mapped[str | None] = mapped_column(Text, nullable=True)
    target: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    reason_txt: Mapped[str | None] = mapped_column(Text, nullable=True)
    conclusion_arr: Mapped[list[int] | None] = mapped_column(
        ARRAY(BigInteger),
        nullable=True,
    )
    conclusion: Mapped[int | None] = mapped_column(BigInteger, nullable=True)


class GdisCurrentValue(ABAIBaseModel):
    """Значение метрики исследования (emg_integration.gdis_current_value)."""

    __tablename__ = "gdis_current_value"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    gdis_curr: Mapped[int] = mapped_column(BigInteger, nullable=False)
    metric: Mapped[int] = mapped_column(BigInteger, nullable=False)
    value_double: Mapped[float | None] = mapped_column(DOUBLE_PRECISION, nullable=True)
    value_string: Mapped[str | None] = mapped_column(Text, nullable=True)
