from datetime import date, datetime

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    Integer,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import SDMOBaseModel


class Station(SDMOBaseModel):
    __tablename__ = "stations"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    place_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    name: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
        comment="имя",
    )
    code: Mapped[str | None] = mapped_column(
        String(40),
        nullable=True,
    )
    IP: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    port: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
        default=1001,
    )
    antena: Mapped[str | None] = mapped_column(
        String(25),
        nullable=True,
    )
    sector: Mapped[str | None] = mapped_column(
        String(25),
        nullable=True,
    )
    active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
        comment="флаг активности",
    )
    server: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    type_1900: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    info: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )
    local_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    server_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    router: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )
    type_electric: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    type_router: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    fc_1549: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    fc_1543: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    router_soft_ver: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    status: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )
    lora: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    lora_deveui: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    lora_server_port: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    network_server_address: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    restart_attempts: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    serial_number: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    check_belt_crash: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )


class FcDataDayParted(SDMOBaseModel):
    __tablename__ = "fc_data_day_parted"

    id: Mapped[int] = mapped_column(
        BigInteger,
        primary_key=True,
        autoincrement=True,
    )
    station_id: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    data: Mapped[dict | list | None] = mapped_column(
        JSON,
        nullable=True,
    )
    savetime: Mapped[datetime] = mapped_column(
        DateTime,
        nullable=False,
        server_default=func.current_timestamp(),
    )
    day: Mapped[date] = mapped_column(
        Date,
        primary_key=True,
        nullable=False,
    )


class FcReg(SDMOBaseModel):
    __tablename__ = "fc_reg"
    __table_args__ = (
        UniqueConstraint("type_1900", "addr", name="type_1900_addr"),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    type_1900: Mapped[int | None] = mapped_column(
        Integer,
        nullable=True,
    )
    addr: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        comment="регистр",
    )
    name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        comment="имя",
    )
    units: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
        comment="единицы измерения",
    )
    koef: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
        comment="коэффициент",
    )
    type: Mapped[str | None] = mapped_column(
        String(15),
        nullable=True,
    )
    dynamic: Mapped[bool | None] = mapped_column(
        Boolean,
        nullable=True,
    )
    info: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    lora_bytes_size: Mapped[int | None] = mapped_column(
        SmallInteger,
        nullable=True,
    )
