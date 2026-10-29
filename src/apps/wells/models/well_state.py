"""Итоговое состояние скважины для матрицы и карточки.

Три таблицы:

- ``wells_well_state`` — журнал состояний: на скважину одна открытая строка
  (``ended_at IS NULL``) — текущее состояние, закрытые — история;
- ``wells_drive_state`` — состояние привода по станции СДМО между прогонами:
  «два отсчёта подряд» и «начало — первый отсчёт» без сканирования истории;
  ``last_seen_at`` — курсор чтения новых отсчётов станции;
- ``wells_state_run`` — прогоны таски по НГДУ: время, сбой СДМО по НГДУ.

Все времена — местное наивное время, проставляется в Python: БД работает в
UTC, и ``server_default=now()`` дал бы сдвиг на 5 часов.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from shared.database.sql.models import AppBaseModel, IntPkMixin

STATE_WORKING = "working"
STATE_WARNING = "warning"
STATE_ALARM = "alarm"
STATE_STOPPED = "stopped"
STATE_REPAIR = "repair"
STATE_NO_LINK = "no_link"
STATE_INACTIVE = "inactive"

STATE_TITLES_RU = {
    STATE_WORKING: "Работает",
    STATE_WARNING: "Отклонение",
    STATE_ALARM: "Авария",
    STATE_STOPPED: "Остановлена",
    STATE_REPAIR: "Ремонт",
    STATE_NO_LINK: "Нет связи",
    STATE_INACTIVE: "Вне эксплуатации",
}

SOURCE_SDMO = "sdmo"
SOURCE_ABAI = "abai"
SOURCE_REPAIR = "repair"
SOURCE_DETECTOR = "detector"


class WellState(AppBaseModel, IntPkMixin):
    __tablename__ = "wells_well_state"
    __table_args__ = (
        Index("ix_wells_well_state_well_id_started_at", "well_id", "started_at"),
        # Одна открытая строка на скважину.
        Index(
            "uq_wells_well_state_open",
            "well_id",
            unique=True,
            postgresql_where=text("ended_at IS NULL"),
        ),
    )

    well_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("wells_well.id"),
        nullable=False,
    )
    # НГДУ (AbaiNGDUIDsEnum), чей прогон пишет строку: по его прогону
    # считается «состояние устарело».
    abai_ngdu_id: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    state: Mapped[str] = mapped_column(String(20), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(40), nullable=False)
    # Текст для оператора, без источников (источник — в ``source``).
    reason_ru: Mapped[str] = mapped_column(Text, nullable=False)
    # Тот же вывод с источниками (СДМО, ABAI) — для разбора и подсказки.
    # NULL у строк, закрытых до появления колонки.
    reason_detail_ru: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Начало по данным (первый отсчёт смены привода, начало статуса ABAI,
    # начало ремонта, эскалация эпизода), не время прогона.
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Входы на момент последнего изменения строки.
    evidence: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WellDriveState(AppBaseModel):
    """Подтверждённое состояние привода станции и кандидат на смену."""

    __tablename__ = "wells_drive_state"

    station_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("telemetry_sdmo_station.id"),
        primary_key=True,
    )
    # Привязка станции на момент расчёта: при перепривязке автомат сбрасывается.
    well_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    running: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Первый отсчёт с другим значением ждёт второго.
    pending_running: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    pending_since: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # После перерыва в данных дольше часа прежнее состояние не используется,
    # пока его не подтвердят два отсчёта.
    reconfirming: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    # Последний пригодный отсчёт (есть статус или обороты) и последний любой —
    # он же курсор чтения новых отсчётов станции.
    last_sample_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class WellStateRun(AppBaseModel):
    """Последний прогон таски по НГДУ."""

    __tablename__ = "wells_state_run"

    abai_ngdu_id: Mapped[int] = mapped_column(SmallInteger, primary_key=True)
    last_run_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    sdmo_outage_since: Mapped[datetime | None] = mapped_column(
        DateTime,
        nullable=True,
    )
    last_duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
