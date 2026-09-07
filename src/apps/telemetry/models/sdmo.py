from datetime import date, datetime

from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Адреса регистров SDMO (union по всем type_1900), из telemetry_sdmo_fc_reg.
# Значения хранятся типизированно (float4) в колонках r_<addr>, а не в JSONB —
# это ~3x компактнее и без повторяющихся строковых ключей в каждой строке.
SDMO_REGISTERS: tuple[int, ...] = (
    4,
    5,
    120,
    190,
    277,
    278,
    341,
    342,
    1502,
    1543,
    1549,
    1610,
    1612,
    1613,
    1614,
    1617,
    1622,
    1630,
    1634,
    1639,
    1660,
    1662,
    1675,
    1676,
    1803,
    1804,
    1810,
    1840,
    1841,
    1842,
    1900,
    1901,
    1902,
    1903,
    1904,
    1908,
    1909,
    1910,
    1912,
    1915,
    1917,
    1918,
    1920,
    1921,
    1922,
    1923,
    1924,
    1925,
    1926,
    1927,
    1928,
    1929,
    1930,
    1931,
    1932,
    1933,
    1934,
    1935,
    1936,
    1937,
    1938,
    1945,
    1946,
    1947,
    1951,
    1956,
    1957,
    1959,
    1969,
    1971,
    1991,
    1995,
    1997,
    1998,
    1999,
    4352,
    4355,
    4356,
    4357,
    4361,
    4363,
    4388,
    8448,
    40000,
    40001,
    41009,
    41010,
    41023,
    41046,
    41196,
    41222,
    60000,
    60001,
    60002,
    60003,
    60004,
    60005,
    60006,
    60007,
    60008,
    60009,
    60010,
    60011,
    60012,
    60032,
    60035,
    60082,
    60128,
)


class SdmoStation(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Станция СДМО (станция управления) из базы одного НГДУ.

    Ключ станции для всей системы — локальный ``id``: на него ссылаются
    ``SdmoFcData.station_id`` и курсоры/инциденты детекторов (``entity_id``).
    Натуральный ``sdmo_id`` уникален только внутри базы своего НГДУ (у каждого
    НГДУ своя MySQL с автоинкрементом от единицы), поэтому строки никогда не
    удаляются и не пересоздаются — только upsert по ``(abai_ngdu_id, sdmo_id)``.
    """

    __tablename__ = "telemetry_sdmo_station"
    __table_args__ = (
        UniqueConstraint(
            "abai_ngdu_id",
            "sdmo_id",
            name="uq_telemetry_sdmo_station_ngdu_sdmo_id",
        ),
    )

    # НГДУ-источник (AbaiNGDUIDsEnum): из какой базы SDMO пришла станция.
    abai_ngdu_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Натуральный ключ — исходный stations.id из БД SDMO своего НГДУ.
    sdmo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    place_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # Имя скважины (Station.code -> Well.name).
    code: Mapped[str | None] = mapped_column(String(40), index=True, nullable=True)
    # Тип станции, нужен для расшифровки регистров по (type_1900, addr).
    type_1900: Mapped[int | None] = mapped_column(Integer, nullable=True)
    serial_number: Mapped[str | None] = mapped_column(String(255), nullable=True)
    active: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    well_id: Mapped[int | None] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=True,
        index=True,
    )


class SdmoFcReg(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "telemetry_sdmo_fc_reg"
    __table_args__ = (
        UniqueConstraint(
            "type_1900",
            "addr",
            name="uq_telemetry_sdmo_fc_reg_type_1900_addr",
        ),
    )

    # Натуральный ключ — исходный fc_reg.id из БД SDMO.
    sdmo_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )
    type_1900: Mapped[int | None] = mapped_column(Integer, nullable=True)
    addr: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    units: Mapped[str | None] = mapped_column(String(45), nullable=True)
    koef: Mapped[float | None] = mapped_column(Float, nullable=True)
    type: Mapped[str | None] = mapped_column(String(15), nullable=True)
    dynamic: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    info: Mapped[str] = mapped_column(Text, nullable=False)
    lora_bytes_size: Mapped[int | None] = mapped_column(Integer, nullable=True)


class SdmoFcData(AppBaseModel, IntPkMixin):
    __tablename__ = "telemetry_sdmo_fc_data"
    # fc_data_day_parted — внутрисуточный ряд (~1 отсчёт / 2 мин на станцию).
    # Натуральный id источника уникален только внутри станции: базы разных НГДУ
    # нумеруют строки независимо. Композитный индекс (station_id, savetime)
    # обслуживает оконные выборки детекторов и «последний отсчёт» матрицы.
    # FK на станцию намеренно нет: проверка FK на COPY в сотни миллионов строк
    # дорога, а целостность держит загрузчик — станции не удаляются.
    __table_args__ = (
        Index(
            "ix_telemetry_sdmo_fc_data_station_savetime",
            "station_id",
            "savetime",
        ),
        Index(
            "uq_telemetry_sdmo_fc_data_station_sdmo_id",
            "station_id",
            "sdmo_id",
            unique=True,
        ),
    )

    # Исходный fc_data_day_parted.id из БД SDMO — tiebreak курсора загрузки
    # внутри станции.
    sdmo_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Локальный ключ станции (== SdmoStation.id).
    station_id: Mapped[int] = mapped_column(Integer, nullable=False)
    # НГДУ-источник (== SdmoStation.abai_ngdu_id); денормализация для фильтров
    # и статистики по НГДУ без join'а станций.
    abai_ngdu_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    day: Mapped[date] = mapped_column(Date, nullable=False)
    savetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)

    # --- Регистры r_<addr> (float4, nullable) ---
    r_4: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_5: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_120: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_190: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_277: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_278: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_341: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_342: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1502: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1543: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1549: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1610: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1612: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1613: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1614: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1617: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1622: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1630: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1634: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1639: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1660: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1662: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1675: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1676: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1803: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1804: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1810: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1840: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1841: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1842: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1900: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1901: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1902: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1903: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1904: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1908: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1909: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1910: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1912: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1915: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1917: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1918: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1920: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1921: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1922: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1923: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1924: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1925: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1926: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1927: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1928: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1929: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1930: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1931: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1932: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1933: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1934: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1935: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1936: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1937: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1938: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1945: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1946: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1947: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1951: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1956: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1957: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1959: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1969: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1971: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1991: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1995: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1997: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1998: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_1999: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4352: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4355: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4356: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4357: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4361: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4363: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_4388: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_8448: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_40000: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_40001: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41009: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41010: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41023: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41046: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41196: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_41222: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60000: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60001: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60002: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60003: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60004: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60005: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60006: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60007: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60008: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60009: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60010: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60011: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60012: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60032: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60035: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60082: Mapped[float | None] = mapped_column(REAL, nullable=True)
    r_60128: Mapped[float | None] = mapped_column(REAL, nullable=True)
