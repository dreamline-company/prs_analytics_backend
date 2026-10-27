"""Досье скважины для системного промпта: главное о скважине одним текстом.

Модель получает досье на каждый вопрос (динамический промпт агента) и знает
основное, даже не вызвав ни одного tool: состояние, дебит, привод, эпизоды
детекторов с отметкой проверки, ремонты, МРП, контур компенсации. Подробности
за период — узкими tools.

Разделы загружаются параллельно, каждый в своей сессии: сбой одного (ошибка
SQL обрывает транзакцию) не роняет остальные — раздел просто выпадает из
текста. Рендер разделов — чистые функции без I/O. Готовый текст кэшируется
на ``CACHE_TTL_SEC`` по скважине.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    local_now,
    rounded,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.incidents import (
    VERDICT_RU,
    list_incident_dtos,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.repairs import (
    REPAIR_CATEGORY,
    list_repairs_with_type,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.status import (
    list_status_rows,
    status_brief,
)
from apps.ai_assistant.ai.well_chat_assistant.tools.telemetry import (
    DRIVE_WORK_CODES,
    LIFT_BY_STATION_TYPE,
    unwrap_moment,
)
from apps.compensation.constants import CONTOUR_NO_PLAN, CONTOUR_STOPPED
from apps.compensation.dto.internal.compensation import CompensationContourDTO
from apps.compensation.dto.queries.compensation import GetCompensationContourQuery
from apps.compensation.use_cases.get_contour import GetCompensationContourUseCase
from apps.detectors.dto.internal.conclusion import WellAiConclusionDTO
from apps.detectors.dto.internal.incident import DetectorIncidentDTO
from apps.detectors.repositories import (
    DetectorConclusionRepository,
    DetectorIncidentRepository,
    DetectorRepository,
    DetectorVerificationRepository,
)
from apps.detectors.services import WellIncidentStatusService
from apps.detectors.use_cases.get_well_ai_conclusion import (
    GetWellAiConclusionUseCase,
)
from apps.detectors.use_cases.list_incidents_by_well_id import (
    ListIncidentsByWellIdUseCase,
)
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.repositories.org import OrgRepository
from apps.org.services import well_name_prefix
from apps.repairs.dto.internal.mrp import WellMrpDTO
from apps.repairs.dto.queries.mrp import GetWellMrpQuery
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.repair import RepairRepository
from apps.repairs.services import CurrentRepairService
from apps.repairs.use_cases.get_well_mrp import GetWellMrpUseCase
from apps.telemetry.repositories.sdmo import (
    SdmoFcDataRepository,
    SdmoFcRegRepository,
    SdmoStationRepository,
)
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.telemetry.repositories.telemetry import TelemetryRepository
from apps.telemetry.services import WellRatesService
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.queries.well import GetWellCardQuery
from apps.wells.models.well import Well
from apps.wells.repositories import WellExplRepository
from apps.wells.repositories.coords import WellCoordRepository
from apps.wells.repositories.gdis import (
    GdisCurrentValueRepository,
    GdisMetricRepository,
)
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.services import CoordPointService, WellGdisService
from apps.wells.use_cases.get_well_card import GetWellCardUseCase
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

CACHE_TTL_SEC = 300
INCIDENTS_WINDOW_DAYS = 90
REPAIRS_WINDOW_DAYS = 365


@dataclass(slots=True)
class DossierData:
    """Сырьё досье; None — раздел не загрузился или данных нет."""

    well_id: int
    well_name: str
    now: datetime
    place: tuple[str, str] | None = None
    lift: str | None = None
    station_type: int | None = None
    abai_status: dict[str, Any] | None = None
    card: WellCardDTO | None = None
    incidents: list[DetectorIncidentDTO] | None = None
    conclusion: WellAiConclusionDTO | None = None
    repairs: list[tuple[Repair, str | None]] | None = None
    mrp: WellMrpDTO | None = None
    contour: CompensationContourDTO | None = None


# --- Рендер: чистые функции ---


def _dt(value: datetime | None) -> str:
    return value.strftime("%d.%m.%Y %H:%M") if value else "—"


def _day(value: date | None) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def _num(value: float | None, unit: str = "") -> str:
    return "—" if value is None else f"{rounded(value, 2):g}{unit}"


def _render_header(data: DossierData) -> list[str]:
    lines = [
        f"Скважина {data.well_name} (well_id {data.well_id}). "
        f"Досье на {_dt(data.now)}, время везде местное.",
    ]
    if data.place:
        lines.append(f"Месторождение: {data.place[0]}; НГДУ: {data.place[1]}.")
    lift = data.lift or LIFT_BY_STATION_TYPE.get(data.station_type)
    if lift:
        lines.append(f"Способ добычи: {lift}.")
    return lines


def _render_state(data: DossierData) -> list[str]:
    status = data.abai_status
    if status is None:
        lines = ["Статус ABAI сейчас: нет данных."]
    else:
        reason = f" — {status['reason']}" if status["reason"] else ""
        since = datetime.fromisoformat(status["since"])
        lines = [f"Статус ABAI сейчас: {status['status']}{reason}, с {_dt(since)}."]
    if data.card is not None:
        repair = data.card.current_repair
        lines.append(
            "Ремонт сейчас не идёт."
            if repair is None
            else f"Идёт ремонт: {repair.repair_type_name_ru or 'вид не указан'}, "
            f"с {_dt(repair.start_time)} (repair_id {repair.id}).",
        )
    return lines


def _render_rates(card: WellCardDTO | None) -> list[str]:
    if card is None:
        return []
    p = card.passport
    deviation = (
        ""
        if p.oil_rate_deviation is None
        else f", отклонение {_num(p.oil_rate_deviation, ' т/сут')}"
        f" ({_num(p.oil_rate_deviation_percent, ' %')})"
    )
    return [
        f"Дебит по последнему замеру ЦИТС ({_dt(p.telemetry_time)}): нефть "
        f"{_num(p.oil_rate, ' т/сут')} при плане {_num(p.plan_oil_rate, ' т/сут')}"
        f"{deviation}; жидкость {_num(p.liquid_rate, ' м³/сут')} при плане "
        f"{_num(p.plan_liquid_rate, ' м³/сут')}; обводнённость "
        f"{_num(p.water_cut, ' %')}. Техрежим с {_day(p.tech_regime_date)}.",
    ]


def _render_drive(card: WellCardDTO | None, station_type: int | None) -> list[str]:
    if card is None:
        return []
    p = card.passport
    lines = []
    if p.sdmo_time is None:
        lines.append("Привод СДМО: станции или отсчётов нет.")
    else:
        if p.sdmo_vlt_status is None:
            drive = "статус неизвестен"
        elif p.sdmo_vlt_status in DRIVE_WORK_CODES:
            drive = "работает"
        else:
            drive = "без тока (остановлен или авария)"
        units = f" {p.pump_speed_units}" if p.pump_speed_units else ""
        values = (
            "момент и скорость в нём не заполнены (динамика — get_drive_telemetry)"
            if p.pump_moment is None and p.pump_speed is None
            else f"момент {_num(unwrap_moment(p.pump_moment, station_type))}, "
            f"скорость {_num(p.pump_speed)}{units}, "
            f"заполнение {_num(p.pump_fill, ' %')}"
        )
        lines.append(
            f"Привод СДМО, последний отсчёт {_dt(p.sdmo_time)}: {values}; "
            f"статус станции {p.sdmo_vlt_status} — {drive}.",
        )
    if p.h_din_m is not None:
        lines.append(
            f"ГДИС: динамический уровень {_num(p.h_din_m, ' м')} "
            f"на {_day(p.h_din_date)}.",
        )
    return lines


def _render_incidents(
    incidents: Sequence[DetectorIncidentDTO] | None,
    now: datetime,
) -> list[str]:
    if incidents is None:
        return []
    active = [item for item in incidents if item.status == "active"]
    lines = (
        [f"Активные эпизоды детекторов: {len(active)}."]
        if active
        else ["Активных эпизодов детекторов нет — детекторы считают работу штатной."]
    )
    for item in active:
        check = ""
        if item.verification is not None:
            verdict = item.verification.verdict
            check = (
                f"; проверка: {VERDICT_RU.get(verdict, verdict)}"
                f" ({item.verification.reason})"
            )
        lines.append(
            f"- {item.detector_code} {item.detector_name_ru or ''}: {item.level}, "
            f"открыт {_dt(item.opened_at)}, последнее подтверждение "
            f"{_dt(item.last_seen_at)}{check} (incident_id {item.id}).",
        )
    since = now - timedelta(days=INCIDENTS_WINDOW_DAYS)
    window = [item for item in incidents if item.opened_at >= since]
    if window:
        counts: dict[str, int] = {}
        for item in window:
            counts[item.detector_code] = counts.get(item.detector_code, 0) + 1
        false_alarms = sum(
            1
            for item in window
            if item.verification and item.verification.verdict == "false_alarm"
        )
        summary = ", ".join(f"{code} — {n}" for code, n in sorted(counts.items()))
        lines.append(
            f"Эпизодов за {INCIDENTS_WINDOW_DAYS} суток: {summary}; "
            f"проверка признала ложными: {false_alarms}.",
        )
    return lines


def _render_conclusion(conclusion: WellAiConclusionDTO | None) -> list[str]:
    if conclusion is None or conclusion.primary is None:
        return []
    primary = conclusion.primary
    return [
        f"ИИ-заключение ({primary.detector_code}): {primary.cause}; "
        f"уверенность {round(primary.confidence * 100)} %.",
    ]


def _render_repairs(
    repairs: Sequence[tuple[Repair, str | None]] | None,
    mrp: WellMrpDTO | None,
    now: datetime,
) -> list[str]:
    lines = []
    if repairs is not None:
        since = now - timedelta(days=REPAIRS_WINDOW_DAYS)
        recent = [repair for repair, _ in repairs if repair.start_time >= since]
        lines.append(f"Ремонтов за {REPAIRS_WINDOW_DAYS} суток: {len(recent)}.")
        if repairs:
            last, type_name = repairs[0]
            category = REPAIR_CATEGORY.get(last.abai_repair_type_id)
            end = "идёт" if last.is_open else _dt(last.end_time)
            lines.append(
                f"Последний ремонт: {type_name or 'вид не указан'}"
                f"{f' ({category})' if category else ''}, {_dt(last.start_time)} — "
                f"{end} (repair_id {last.id}).",
            )
    if mrp is not None and mrp.avg_mrp_days is not None:
        lines.append(
            f"МРП: средний {_num(mrp.avg_mrp_days, ' сут')} по "
            f"{mrp.intervals_count} интервалам; после ремонта МРП сократился "
            f"в {mrp.worse_count} случаях.",
        )
    return lines


def _render_contour(contour: CompensationContourDTO | None) -> list[str]:
    if contour is None:
        return []
    if contour.state == CONTOUR_STOPPED and contour.losses is not None:
        return [
            f"Контур компенсации: стоит, потеря {_num(contour.losses.value, ' т/сут')}"
            f", компенсировано {_num(contour.compensated.value, ' т/сут')} "
            f"({contour.compensated.donors} доноров).",
        ]
    if contour.state == CONTOUR_NO_PLAN:
        return ["Контур компенсации: стоит, но плана по нефти нет."]
    return []


def render_dossier(data: DossierData) -> str:
    return "\n".join(
        [
            *_render_header(data),
            *_render_state(data),
            *_render_rates(data.card),
            *_render_drive(data.card, data.station_type),
            *_render_incidents(data.incidents, data.now),
            *_render_conclusion(data.conclusion),
            *_render_repairs(data.repairs, data.mrp, data.now),
            *_render_contour(data.contour),
        ],
    )


# --- Загрузка разделов ---


async def _load_card(session: AsyncSession, well: Well) -> WellCardDTO:
    return await GetWellCardUseCase(
        well_repository=WellRepository(session=session),
        well_rates_service=WellRatesService(
            telemetry_repository=TelemetryRepository(session=session),
            tech_regime_repository=TechRegimeRepository(session=session),
        ),
        sdmo_station_repository=SdmoStationRepository(session=session),
        sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
        sdmo_fc_reg_repository=SdmoFcRegRepository(session=session),
        well_status_history_repository=WellStatusHistoryRepository(session=session),
        coord_point_service=CoordPointService(
            well_coord_repository=WellCoordRepository(session=session),
        ),
        well_incident_status_service=WellIncidentStatusService(
            incident_repository=DetectorIncidentRepository(session=session),
            detector_repository=DetectorRepository(session=session),
        ),
        current_repair_service=CurrentRepairService(
            repair_repository=RepairRepository(session=session),
        ),
        well_gdis_service=WellGdisService(
            gdis_metric_repository=GdisMetricRepository(session=session),
            gdis_current_value_repository=GdisCurrentValueRepository(
                session=session,
            ),
        ),
    ).execute(GetWellCardQuery(well_id=well.id))


async def _load_place(session: AsyncSession, well: Well) -> tuple[str, str] | None:
    prefix = well_name_prefix(well.name)
    if prefix is None:
        return None
    fields = await OilFieldRepository(session).list_by_prefix(prefix)
    if not fields:
        return None
    ngdu = await OrgRepository(session).get_by_id(fields[0].ngdu_id)
    return fields[0].name, ngdu.name_ru if ngdu else "—"


async def _load_lift(session: AsyncSession, well: Well) -> str | None:
    names = await WellExplRepository(
        session,
    ).get_latest_expl_name_by_abai_well_ids([well.abai_id])
    return names.get(well.abai_id)


async def _load_station_type(session: AsyncSession, well: Well) -> int | None:
    stations = await SdmoStationRepository(session).list_by_well_id(well_id=well.id)
    return next((s.type_1900 for s in stations if s.type_1900), None)


async def _load_abai_status(session: AsyncSession, well: Well) -> dict | None:
    now = local_now()
    rows = await list_status_rows(
        session,
        well.abai_id,
        since=now,
        until=now + timedelta(seconds=1),
    )
    return status_brief(rows[-1]) if rows else None


async def _load_incidents(
    session: AsyncSession,
    well: Well,
) -> list[DetectorIncidentDTO]:
    return await list_incident_dtos(
        ListIncidentsByWellIdUseCase(
            incident_repository=DetectorIncidentRepository(session),
            detector_repository=DetectorRepository(session),
            verification_repository=DetectorVerificationRepository(session),
        ),
        well_id=well.id,
        days=INCIDENTS_WINDOW_DAYS,
    )


async def _load_conclusion(session: AsyncSession, well: Well) -> WellAiConclusionDTO:
    return await GetWellAiConclusionUseCase(
        incident_repository=DetectorIncidentRepository(session=session),
        conclusion_repository=DetectorConclusionRepository(session=session),
        detector_repository=DetectorRepository(session=session),
    ).execute(well.id)


async def _load_repairs(
    session: AsyncSession,
    well: Well,
) -> list[tuple[Repair, str | None]]:
    return await list_repairs_with_type(session, well.abai_id)


async def _load_mrp(session: AsyncSession, well: Well) -> WellMrpDTO:
    return await GetWellMrpUseCase(
        repair_repository=RepairRepository(session=session),
        well_repository=WellRepository(session=session),
    ).execute(GetWellMrpQuery(well_id=well.id))


async def _load_contour(session: AsyncSession, well: Well) -> CompensationContourDTO:
    return await GetCompensationContourUseCase(session).execute(
        GetCompensationContourQuery(well_id=well.id),
    )


type Loader = Callable[[AsyncSession, Well], Awaitable[Any]]

# Поле DossierData -> загрузчик раздела.
LOADERS: dict[str, Loader] = {
    "card": _load_card,
    "place": _load_place,
    "lift": _load_lift,
    "station_type": _load_station_type,
    "abai_status": _load_abai_status,
    "incidents": _load_incidents,
    "conclusion": _load_conclusion,
    "repairs": _load_repairs,
    "mrp": _load_mrp,
    "contour": _load_contour,
}


async def _load_section(name: str, loader: Loader, well: Well) -> Any:  # noqa: ANN401
    try:
        async with session_makers["app"]() as session:
            return await loader(session, well)
    except Exception:
        logger.exception("Well dossier section %s failed: well=%s", name, well.id)
        return None


async def build_well_dossier(well_id: int) -> str | None:
    """Досье скважины; None — скважины нет."""
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
    if well is None:
        return None
    results = await asyncio.gather(
        *(_load_section(name, loader, well) for name, loader in LOADERS.items()),
    )
    data = DossierData(well_id=well.id, well_name=well.name, now=local_now())
    for name, value in zip(LOADERS, results, strict=True):
        setattr(data, name, value)
    return render_dossier(data)


class WellDossierProvider:
    """Досье по скважине с кэшем на ``ttl_sec`` в памяти процесса."""

    def __init__(self, ttl_sec: float = CACHE_TTL_SEC) -> None:
        self.ttl_sec = ttl_sec
        self._cache: dict[int, tuple[float, str | None]] = {}

    async def get(self, well_id: int) -> str | None:
        cached = self._cache.get(well_id)
        if cached is not None and cached[0] > time.monotonic():
            return cached[1]
        try:
            text = await build_well_dossier(well_id)
        except Exception:
            logger.exception("Well dossier failed: well=%s", well_id)
            return None
        self._cache[well_id] = (time.monotonic() + self.ttl_sec, text)
        return text


well_dossier_provider = WellDossierProvider()
