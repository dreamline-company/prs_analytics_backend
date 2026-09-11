# ruff: noqa: TC002
"""Tools скважинного ассистента: карточка, эпизоды, заключение, телеметрия.

Текущий ``well_id`` инжектится через ``RunnableConfig.configurable``. Каждый
tool открывает собственную сессию БД (см. принцип в tools ПРС-ассистента) и
возвращает короткие высокосигнальные поля — большие блобы наружу не отдаются.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorIncidentRepository,
    DetectorRepository,
)
from apps.detectors.use_cases.get_well_ai_conclusion import (
    GetWellAiConclusionUseCase,
)
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.repair import RepairRepository
from apps.telemetry.repositories.sdmo import (
    ROTOR_SPEED_REGISTER,
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services.sdmo_scale import (
    PUMP_PARAMETER_REGISTERS,
    SdmoRegisterScaler,
)
from apps.telemetry.services.well_rates import WellRatesService
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers
from shared.repository.sqlalchemy import QuerySpec

RATES_HISTORY_DAYS = 30
RECENT_REPAIRS_LIMIT = 10


class WellContextMissingError(RuntimeError):
    """Raised when a tool is invoked without a well bound to the chat."""

    default_message = "well_id is not set for this chat. Ask the user to pick a well."

    def __init__(self) -> None:
        super().__init__(self.default_message)


def _get_well_id(config: RunnableConfig) -> int:
    configurable = (config or {}).get("configurable") or {}
    well_id = configurable.get("well_id")
    if not isinstance(well_id, int):
        raise WellContextMissingError
    return well_id


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


@tool
async def get_well_overview(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает паспорт текущей скважины: дебиты факт/план, обводнённость,
    времена последних отсчётов источников и серийник станции управления.

    Вызывайте этот tool первым, чтобы понять, о какой скважине речь.
    """
    well_id = _get_well_id(config)
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return {"error": "well_not_found", "well_id": well_id}

        rates = await WellRatesService(
            TelemetryRepository(session),
            TechRegimeRepository(session),
        ).get_for_well(well_id=well.id, abai_well_id=well.abai_id)

        stations = await SdmoStationRepository(session).list_by_well_id(
            well_id=well.id,
        )
        serial = next(
            (station.serial_number for station in stations if station.serial_number),
            None,
        )
        return {
            "well_id": well.id,
            "well_name": well.name,
            "device_serial": serial,
            "oil_rate": rates.oil_rate,
            "liquid_rate": rates.liquid_rate,
            "water_cut": rates.water_cut,
            "telemetry_time": _iso(rates.telemetry_time),
            "plan_oil_rate": rates.plan_oil_rate,
            "plan_liquid_rate": rates.plan_liquid_rate,
            "tech_regime_date": (
                rates.tech_regime_date.isoformat() if rates.tech_regime_date else None
            ),
        }


@tool
async def get_active_incidents(config: RunnableConfig) -> list[dict[str, Any]]:
    """Возвращает активные эпизоды детекции по скважине (R2 — обрыв штанг,
    R9 — перекос нагрузки/утечка): уровень, когда открыт, ключевые улики."""
    well_id = _get_well_id(config)
    async with session_makers["app"]() as session:
        incidents = await DetectorIncidentRepository(
            session,
        ).list_active_by_well_id(well_id)
        result = []
        for incident in incidents:
            payload = incident.payload or {}
            result.append(
                {
                    "incident_id": incident.id,
                    "detector_code": incident.detector_code,
                    "reason_code": incident.reason_code,
                    "level": incident.level,
                    "opened_at": _iso(incident.opened_at),
                    "last_seen_at": _iso(incident.last_seen_at),
                    "escalated_at": _iso(incident.escalated_at),
                    # Ключевые цифры без полной истории улик — экономия токенов.
                    "current_ratio": payload.get("current_ratio"),
                    "k": payload.get("k"),
                    "branches": payload.get("branches"),
                },
            )
        return result


@tool
async def get_ai_conclusion(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает актуальное ИИ-заключение по скважине: причина, уверенность,
    рекомендации и summary. primary — эпизод худшего уровня."""
    well_id = _get_well_id(config)
    async with session_makers["app"]() as session:
        conclusion = await GetWellAiConclusionUseCase(
            incident_repository=DetectorIncidentRepository(session=session),
            conclusion_repository=DetectorConclusionRepository(session=session),
            detector_repository=DetectorRepository(session=session),
        ).execute(well_id)
        return conclusion.model_dump(mode="json")


@tool
async def get_pump_telemetry(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает последний отсчёт телеметрии насоса по станциям СДМО
    скважины: момент, скорость, заполнение и время отсчёта."""
    well_id = _get_well_id(config)
    async with session_makers["app"]() as session:
        stations = await SdmoStationRepository(session).list_by_well_id(
            well_id=well_id,
        )
        if not stations:
            return {"error": "no_sdmo_stations", "well_id": well_id}
        pump = await SdmoFcDataRepository(session).get_last_pump_parameters_by_stations(
            station_ids=[station.id for station in stations],
        )
        if pump is None:
            return {"error": "no_sdmo_data", "well_id": well_id}
        # koef справочника по типу станции; без типа — сырые значения и
        # sdmo_scaled=False, чтобы модель не выдавала их за физические.
        station_type = next(
            (s.type_1900 for s in stations if s.id == pump["station_id"]),
            None,
        )
        scaler = await SdmoRegisterScaler.load(SdmoFcRegRepository(session))
        scaled = scaler.scale_row(
            pump,
            type_1900=station_type,
            registers=PUMP_PARAMETER_REGISTERS,
        )
        return {
            **scaled.values,
            "pump_speed_units": scaler.units(
                addr=ROTOR_SPEED_REGISTER,
                type_1900=station_type,
            ),
            "sdmo_scaled": scaled.scaled,
            "sdmo_time": _iso(pump["savetime"]),
        }


@tool
async def get_rates_history_30d(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает историю дебитов скважины за 30 суток (факт по телеметрии)
    и текущий план техрежима — для сравнения «факт vs план»."""
    well_id = _get_well_id(config)
    since = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        days=RATES_HISTORY_DAYS,
    )
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
        return {
            "plan_oil_rate": regime.oil if regime else None,
            "plan_liquid_rate": regime.liquid if regime else None,
            "history": [
                {
                    "date_time": _iso(row.date_time),
                    "oil_rate": row.qm_oil,
                    "liquid_rate": row.qv_liquid,
                }
                for row in rows
            ],
        }


@tool
async def list_recent_repairs(config: RunnableConfig) -> list[dict[str, Any]]:
    """Возвращает последние ремонты скважины: даты, план и факт работ."""
    well_id = _get_well_id(config)
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return []
        repairs = await RepairRepository(session).get_list(
            QuerySpec(
                filters=(Repair.abai_well_id == well.abai_id,),
                order_by=(Repair.start_time.desc(),),
                limit=RECENT_REPAIRS_LIMIT,
            ),
        )
        return [
            {
                "repair_id": repair.id,
                "start_time": _iso(repair.start_time),
                "end_time": _iso(repair.end_time),
                "is_active": repair.end_time is None,
                "work_plan": repair.work_plan,
                "work_list": repair.work_list,
            }
            for repair in repairs
        ]


WELL_CHAT_ASSISTANT_TOOLS = [
    get_well_overview,
    get_active_incidents,
    get_ai_conclusion,
    get_pump_telemetry,
    get_rates_history_30d,
    list_recent_repairs,
]
