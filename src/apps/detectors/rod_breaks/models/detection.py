from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin, TimedMixinModel

# Класс события отказа (восстанавливается из телеметрии, см. spec раздел 5).
EVENT_CLASS_ACTIONABLE = "actionable"
EVENT_CLASS_FAILED_LONG_BEFORE = "failed_long_before_repair"
EVENT_CLASS_ALREADY_STOPPED = "already_stopped"

# Статусы прогона детектора.
RUN_STATUS_RUNNING = "running"
RUN_STATUS_COMPLETED = "completed"
RUN_STATUS_FAILED = "failed"


class RodBreakRun(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Один прогон детектора обрыва штанги по флоту на дату as_of."""

    __tablename__ = "detector_rod_break_run"

    # Дата, на которую считается детекция (правый край 60-дневного окна).
    as_of_date: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    wells_scanned: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detections_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # Версия набора порогов (config.CONFIG_VERSION) — для воспроизводимости.
    config_version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=RUN_STATUS_RUNNING,
    )


class RodBreakDetection(AppBaseModel, IntPkMixin, TimedMixinModel):
    """Результат применения правила R2 к одной скважине за один прогон."""

    __tablename__ = "detector_rod_break_detection"

    run_id: Mapped[int] = mapped_column(
        ForeignKey("detector_rod_break_run.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    well_id: Mapped[int] = mapped_column(
        ForeignKey("wells_well.id"),
        index=True,
        nullable=False,
    )
    # SdmoStation.sdmo_id, по которой считалась детекция.
    station_sdmo_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Код правила. Пока единственный детектор — R2 (обрыв штанги).
    detector_code: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        default="R2",
    )
    fired: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Дата первой корзины сработавшей серии.
    fired_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Фактическая дата отказа, восстановленная из телеметрии.
    failure_dt: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Запас по времени: failure_dt - fired_at, в часах (может быть отрицательным).
    lead_time_hours: Mapped[float | None] = mapped_column(Float, nullable=True)
    event_class: Mapped[str | None] = mapped_column(String(30), nullable=True)
    # Базовый момент скважины за окно (медиана) — для оценки надёжности порога.
    base_moment: Mapped[float | None] = mapped_column(Float, nullable=True)
    # base_moment < MIN_BASE_MOMENT: универсальный порог 0.4 ненадёжен.
    low_confidence: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # Корзины-улики (сериализованные Bucket2h вокруг сработки) для аудита.
    evidence: Mapped[dict | list | None] = mapped_column(JSONB, nullable=True)
