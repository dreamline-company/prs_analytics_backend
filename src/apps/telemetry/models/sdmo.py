from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel


class SdmoStation(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "telemetry_sdmo_station"

    # Натуральный ключ — исходный stations.id из БД SDMO.
    sdmo_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )
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


class SdmoFcData(AppBaseModel, IntPkMixin, TimedMixinModel):
    __tablename__ = "telemetry_sdmo_fc_data"
    # ВНИМАНИЕ: fc_data_day_parted — НЕ одна строка в сутки, а внутрисуточный ряд
    # (~1 отсчёт каждые 2 минуты на станцию). Уникальность держим по натуральному
    # ключу строки-источника (sdmo_id), а не по (station, day). Композитный индекс
    # (sdmo_station_id, savetime) обслуживает оконные выборки детекторов.
    __table_args__ = (
        Index(
            "ix_telemetry_sdmo_fc_data_station_savetime",
            "sdmo_station_id",
            "savetime",
        ),
    )

    # Исходный fc_data_day_parted.id из БД SDMO — курсор инкрементальной загрузки.
    sdmo_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        index=True,
        nullable=False,
    )
    # Ссылка на станцию (== SdmoStation.sdmo_id).
    sdmo_station_id: Mapped[int] = mapped_column(Integer, index=True, nullable=False)
    day: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    savetime: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    # Карта регистров {addr: value}, расшифровывается через telemetry_sdmo_fc_reg.
    data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
