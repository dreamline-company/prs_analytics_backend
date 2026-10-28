# ruff: noqa: TC002
"""Tools по телеметрии: привод СДМО по часам, замеры дебита ЦИТС."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    clamp,
    get_well_id,
    iso,
    local_now,
    rounded,
)
from apps.telemetry.repositories.sdmo import (
    ENGINE_CURRENT_REGISTER,
    PUMP_MOMENT_REGISTER,
    ROTOR_SPEED_REGISTER,
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.drive import DRIVE_RUNNING_VLT_CODES
from apps.telemetry.services.sdmo_scale import SdmoRegisterScaler
from apps.telemetry.services.well_rates import water_cut
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers

DRIVE_DEFAULT_HOURS = 24
DRIVE_MAX_HOURS = 72
# До суток — по часу; длиннее окно — корзины по 3 ч, иначе 72 строки съедают
# треть окна модели.
DRIVE_HOURLY_UP_TO = 24
DRIVE_COARSE_BUCKET_HOURS = 3
DRIVE_COLUMNS = (
    "from",
    "samples",
    "drive_working_share",
    "rotating_share",
    "speed_median",
    "moment_median",
    "moment_min",
    "moment_max",
    "current_median",
)
RATES_DEFAULT_DAYS = 30
RATES_MAX_DAYS = 90
RATES_LIMIT = 60
RATES_COLUMNS = ("time", "oil_t_day", "liquid_m3_day", "water_cut_pct")

# Типы станций (type_1900): у ШГН момент 1991 едет uint16 — отрицательный
# приезжает как 65536 − x; у ЭВН встречаются Int32-выбросы (-4292, 65518).
STATION_TYPE_SHGN = 1
STATION_TYPE_EVN = 6
EVN_MOMENT_CLIP = (0.0, 10000.0)
UINT16_SIGN_LIMIT = 32767
UINT16_PERIOD = 65536
LIFT_BY_STATION_TYPE = {STATION_TYPE_SHGN: "ШГН", STATION_TYPE_EVN: "ЭВН"}


def unwrap_moment(value: float | None, type_1900: int | None) -> float | None:
    """Момент ШГН приезжает uint16: отрицательный — как 65536 − x."""
    if type_1900 != STATION_TYPE_SHGN or value is None or value <= UINT16_SIGN_LIMIT:
        return value
    return value - UINT16_PERIOD


def _share(part: int, total: int) -> float | None:
    return round(part / total, 2) if total else None


def _fmt_time(value: datetime) -> str:
    """Короткая метка для табличных рядов — экономит токены против ISO."""
    return value.strftime("%d.%m %H:%M")


@tool
async def get_drive_telemetry(
    config: RunnableConfig,
    hours: int = DRIVE_DEFAULT_HOURS,
) -> dict[str, Any]:
    """Работа привода по станции СДМО за последние ``hours`` часов (по
    умолчанию 24, максимум 72). Таблица ``rows`` по столбцам ``columns``:
    до 24 часов — по часу, больше — по 3 часа. В строке: начало интервала,
    число отсчётов, доля времени, когда привод работал (по статусу станции
    1999), доля отсчётов с вращением ротора, медиана скорости, медиана /
    минимум / максимум момента, медиана тока двигателя.

    Интервалов без отсчётов в таблице нет — это пропуск связи, а не нули.
    Используй для вопросов «работает ли привод», «когда остановили»,
    «как менялся момент», «что было перед тревогой».
    """
    well_id = get_well_id(config)
    hours = clamp(hours, 1, DRIVE_MAX_HOURS)
    bucket_hours = 1 if hours <= DRIVE_HOURLY_UP_TO else DRIVE_COARSE_BUCKET_HOURS
    async with session_makers["app"]() as session:
        stations = await SdmoStationRepository(session).list_by_well_id(
            well_id=well_id,
        )
        if not stations:
            return {"error": "no_sdmo_stations", "well_id": well_id}
        fc_data = SdmoFcDataRepository(session)
        last = await fc_data.get_last_pump_parameters_by_stations(
            station_ids=[station.id for station in stations],
        )
        if last is None:
            return {"error": "no_sdmo_data", "well_id": well_id}
        # Лента — станция с самым свежим отсчётом.
        station = next(s for s in stations if s.id == last["station_id"])
        until = local_now()
        rows = await fc_data.list_drive_stats(
            station.id,
            since=until - timedelta(hours=hours),
            until=until,
            bucket_hours=bucket_hours,
            drive_work_codes=sorted(DRIVE_RUNNING_VLT_CODES),
            moment_clip=(
                EVN_MOMENT_CLIP if station.type_1900 == STATION_TYPE_EVN else None
            ),
            moment_wrap=station.type_1900 == STATION_TYPE_SHGN,
        )
        scaler = await SdmoRegisterScaler.load(SdmoFcRegRepository(session))

    def scale(value: float | None, addr: int) -> float | None:
        return rounded(scaler.scale(value, addr=addr, type_1900=station.type_1900))

    return {
        "station_serial": station.serial_number,
        "lift_type": LIFT_BY_STATION_TYPE.get(station.type_1900),
        "speed_units": scaler.units(
            addr=ROTOR_SPEED_REGISTER,
            type_1900=station.type_1900,
        ),
        "last_sample_time": iso(last["savetime"]),
        "hours": hours,
        "bucket_hours": bucket_hours,
        "columns": DRIVE_COLUMNS,
        "rows": [
            [
                _fmt_time(row["bucket"]),
                row["samples"],
                _share(row["working"], row["status_samples"]),
                _share(row["rotating"], row["speed_samples"]),
                scale(row["speed_median"], ROTOR_SPEED_REGISTER),
                scale(row["moment_median"], PUMP_MOMENT_REGISTER),
                scale(row["moment_min"], PUMP_MOMENT_REGISTER),
                scale(row["moment_max"], PUMP_MOMENT_REGISTER),
                scale(row["current_median"], ENGINE_CURRENT_REGISTER),
            ]
            for row in rows
        ],
    }


@tool
async def get_rates_history(
    config: RunnableConfig,
    days: int = RATES_DEFAULT_DAYS,
) -> dict[str, Any]:
    """Замеры дебита ЦИТС за последние ``days`` суток (по умолчанию 30,
    максимум 90): таблица ``rows`` по столбцам ``columns`` — время замера,
    нефть т/сут, жидкость м³/сут, обводнённость %; плюс план по последнему
    техрежиму — для сравнения «факт vs план». Отдаются последние 60 замеров."""
    well_id = get_well_id(config)
    days = clamp(days, 1, RATES_MAX_DAYS)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days)
    async with session_makers["app"]() as session:
        rows = await TelemetryRepository(session).list_by_well_id_in_period(
            well_id,
            date_time_from=since,
        )
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        regime = None
        if well is not None:
            regime = await TechRegimeRepository(session).get_last_by_abai_well_id(
                well.abai_id,
            )
    rows = list(rows)[-RATES_LIMIT:]
    return {
        "days": days,
        "plan_oil_rate": regime.oil if regime else None,
        "plan_liquid_rate": regime.liquid if regime else None,
        "tech_regime_date": iso(regime.start_date) if regime else None,
        "columns": RATES_COLUMNS,
        "rows": [
            [
                _fmt_time(row.date_time),
                rounded(row.qm_oil, 2),
                rounded(row.qv_liquid, 2),
                rounded(water_cut(liquid_rate=row.qv_liquid, oil_rate=row.qm_oil)),
            ]
            for row in rows
        ],
    }
