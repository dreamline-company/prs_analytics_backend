from datetime import datetime

from sqlalchemy import DateTime, Float, String
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import WinccTelemetryBaseModel


class NGDUWinccTelemetryModel(WinccTelemetryBaseModel):
    __abstract__ = True
    __table_args__ = {"schema": "dbo"}

    Meas_date: Mapped[datetime] = mapped_column(
        DateTime,
        primary_key=True,
        nullable=False,
    )
    Oil_field: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        nullable=False,
    )
    Well: Mapped[str] = mapped_column(
        String,
        primary_key=True,
        nullable=True,
    )
    Qv_liq: Mapped[float] = mapped_column(Float, nullable=True)
    Qm_oil: Mapped[float] = mapped_column(Float, nullable=True)


class KainarWinccTelemetry(NGDUWinccTelemetryModel):
    __tablename__ = "EMG-KMG-TM"


class DMGWinccTelemetry(NGDUWinccTelemetryModel):
    __tablename__ = "DMG_CITSS_TM"
