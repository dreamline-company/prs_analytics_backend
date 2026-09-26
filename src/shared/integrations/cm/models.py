from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import CMBaseModel, IntPkMixin


class NGDU(CMBaseModel, IntPkMixin):
    __tablename__ = "main_ngdu"

    name: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    def __str__(self) -> str:
        return self.name


class Brigade(CMBaseModel, IntPkMixin):
    __tablename__ = "main_brigade"

    name: Mapped[str] = mapped_column(
        String(25),
        nullable=False,
    )

    cdng: Mapped[str | None] = mapped_column(
        String(25),
        nullable=True,
    )

    ngdu_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("main_ngdu.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    fio: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    lift: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    field: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    device: Mapped[str | None] = mapped_column(
        String(50),
        nullable=True,
    )

    def __str__(self) -> str:
        return f"{self.name} - {self.ngdu.name}"


class BrigadeErrorScreen(CMBaseModel, IntPkMixin):
    __tablename__ = "main_brigadeerrorscreen"

    brigade_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("main_brigade.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    # Django FileField по умолчанию хранит путь в varchar(100)
    screen: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    is_processed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        server_default="false",
    )

    # В CM колонка — timestamp with time zone: с timezone=True драйвер принимает
    # aware-границы в фильтрах и отдаёт aware-значения (репозиторий приводит их
    # к наивному местному времени приложения).
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
