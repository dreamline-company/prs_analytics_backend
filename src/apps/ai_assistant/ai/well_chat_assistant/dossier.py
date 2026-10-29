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
from apps.ai_assistant.ai.well_chat_assistant.tools.compensation import (
    DONOR_STATUS_RU,
    RISK_RU,
    donor_unavailable_reason,
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
    LIFT_BY_STATION_TYPE,
    unwrap_moment,
)
from apps.compensation.constants import CONTOUR_NO_PLAN, CONTOUR_STOPPED
from apps.compensation.dto.internal.compensation import CompensationContourDTO
from apps.compensation.dto.queries.compensation import GetCompensationContourQuery
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.compensation.services.state import CompensationStateService, utc_now
from apps.compensation.services.view import donor_status
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
from apps.telemetry.services.drive import vlt_running
from apps.wells.dto.internal.well_card import WellCardDTO
from apps.wells.dto.queries.well import GetWellCardQuery
from apps.wells.models.well import Well
from apps.wells.repositories import (
    WellExplRepository,
    WellOrgRepository,
    WellStatusRepository,
)
from apps.wells.repositories.coords import WellCoordRepository
from apps.wells.repositories.gdis import (
    GdisCurrentValueRepository,
    GdisMetricRepository,
)
from apps.wells.repositories.status_history import WellStatusHistoryRepository
from apps.wells.repositories.well import WellRepository
from apps.wells.repositories.well_state import (
    WellStateRepository,
    WellStateRunRepository,
)
from apps.wells.services import CoordPointService, NGDUWellsService, WellGdisService
from apps.wells.services.production_profile import WellProductionProfileService
from apps.wells.services.well_state.read import WellStateReadService
from apps.wells.use_cases.get_well_card import GetWellCardUseCase
from core import get_logger
from shared.database.sql.setup import session_makers

logger = get_logger(__name__)

CACHE_TTL_SEC = 300
INCIDENTS_WINDOW_DAYS = 90
REPAIRS_WINDOW_DAYS = 365
# Сколько разделов одного досье грузится одновременно: у каждого своя сессия,
# а пул движка app (5 + 10) общий с HTTP-эндпоинтами процесса.
PARALLEL_SECTIONS = 3


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
    # Правило -> (эпизодов за окно, из них ложных) — без лимита ленты.
    incident_counts: dict[str, tuple[int, int]] | None = None
    conclusion: WellAiConclusionDTO | None = None
    repairs: list[tuple[Repair, str | None]] | None = None
    mrp: WellMrpDTO | None = None
    contour: CompensationContourDTO | None = None
    donor: dict[str, Any] | None = None  # скважина в пуле доноров


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
            drive = "статус станции неизвестен"
        elif vlt_running(p.sdmo_vlt_status) is True:
            drive = f"статус станции {p.sdmo_vlt_status} — работает"
        elif vlt_running(p.sdmo_vlt_status) is False:
            drive = (
                f"статус станции {p.sdmo_vlt_status} — без тока (остановлен или авария)"
            )
        else:
            drive = f"статус станции {p.sdmo_vlt_status} — код неизвестен"
        units = f" {p.pump_speed_units}" if p.pump_speed_units else ""
        values = (
            "момент и скорость в нём не заполнены (динамика — get_drive_telemetry)"
            if p.pump_moment is None and p.pump_speed is None
            else f"момент {_num(unwrap_moment(p.pump_moment, station_type))}, "
            f"скорость {_num(p.pump_speed)}{units}, "
            f"заполнение {_num(p.pump_fill, ' %')}"
        )
        lines.append(
            f"Привод СДМО, последний отсчёт {_dt(p.sdmo_time)}: {values}; {drive}.",
        )
    if p.h_din_m is not None:
        lines.append(
            f"ГДИС: динамический уровень {_num(p.h_din_m, ' м')} "
            f"на {_day(p.h_din_date)}.",
        )
    return lines


def _render_incidents(
    incidents: Sequence[DetectorIncidentDTO] | None,
    counts: dict[str, tuple[int, int]] | None,
) -> list[str]:
    lines = [] if incidents is None else _render_active(incidents)
    if counts:
        summary = ", ".join(f"{code} — {n}" for code, (n, _) in sorted(counts.items()))
        false_alarms = sum(false for _, false in counts.values())
        lines.append(
            f"Эпизодов за {INCIDENTS_WINDOW_DAYS} суток: {summary}; "
            f"проверка признала ложными: {false_alarms}.",
        )
    elif counts is not None:
        lines.append(f"Эпизодов за {INCIDENTS_WINDOW_DAYS} суток не было.")
    return lines


def _render_active(incidents: Sequence[DetectorIncidentDTO]) -> list[str]:
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


def _render_donor(donor: dict[str, Any] | None) -> list[str]:
    if donor is None:
        return []
    status = DONOR_STATUS_RU.get(donor["status"], donor["status"])
    assigned = f", закреплён за {donor['loss_well']}" if donor["loss_well"] else ""
    unavailable = (
        f" Сейчас как донор недоступен — {donor['unavailable']}."
        if donor["unavailable"]
        else ""
    )
    return [
        f"Донор контура компенсации: {status}{assigned}; разгон привода на "
        f"{donor['step_percent']} % даст {_num(donor['gain'], ' т/сут')} нефти "
        f"(риск {RISK_RU.get(donor['risk'], donor['risk'])}).{unavailable}",
    ]


def render_dossier(data: DossierData) -> str:
    return "\n".join(
        [
            *_render_header(data),
            *_render_state(data),
            *_render_rates(data.card),
            *_render_drive(data.card, data.station_type),
            *_render_incidents(data.incidents, data.incident_counts),
            *_render_conclusion(data.conclusion),
            *_render_repairs(data.repairs, data.mrp, data.now),
            *_render_contour(data.contour),
            *_render_donor(data.donor),
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
        well_state_read_service=WellStateReadService(
            state_repository=WellStateRepository(session=session),
            run_repository=WellStateRunRepository(session=session),
        ),
        production_profile_service=WellProductionProfileService(
            telemetry_repository=TelemetryRepository(session=session),
            tech_regime_repository=TechRegimeRepository(session=session),
            sdmo_station_repository=SdmoStationRepository(session=session),
            sdmo_fc_data_repository=SdmoFcDataRepository(session=session),
            well_status_repository=WellStatusRepository(session=session),
        ),
    ).execute(GetWellCardQuery(well_id=well.id))


async def _load_place(session: AsyncSession, well: Well) -> tuple[str, str] | None:
    """Месторождение и НГДУ. Один префикс может числиться за несколькими НГДУ —
    тогда берётся то месторождение, в чей круг скважин входит эта."""
    prefix = well_name_prefix(well.name)
    if prefix is None:
        return None
    oil_fields = await OilFieldRepository(session).list_by_prefix(prefix)
    oil_field = oil_fields[0] if len(oil_fields) == 1 else None
    if len(oil_fields) > 1:
        org_repository = OrgRepository(session)
        wells_service = NGDUWellsService(
            org_repository=org_repository,
            well_repository=WellRepository(session),
            well_org_repository=WellOrgRepository(session),
            oil_field_repository=OilFieldRepository(session),
        )
        for candidate in oil_fields:
            scope = await wells_service.list_wells(
                candidate.ngdu_id,
                oil_field_id=candidate.id,
            )
            if any(item.id == well.id for item in scope):
                oil_field = candidate
                break
    if oil_field is None:
        return None
    ngdu = await OrgRepository(session).get_by_id(oil_field.ngdu_id)
    return oil_field.name, ngdu.name_ru if ngdu else "—"


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


async def _load_incident_counts(
    session: AsyncSession,
    well: Well,
) -> dict[str, tuple[int, int]]:
    return await DetectorIncidentRepository(session).count_by_detector_since(
        well_id=well.id,
        opened_from=local_now() - timedelta(days=INCIDENTS_WINDOW_DAYS),
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


async def _load_donor(session: AsyncSession, well: Well) -> dict[str, Any] | None:
    """Строка пула доноров без загрузки всего месторождения — для досье.

    Скважины КМГ вне VMB в пуле есть, но в контур не входят, и подбор их
    никогда не возьмёт — донором в досье они не называются.
    """
    donors = await CompensationDonorRepository(session).list_by_well_ids([well.id])
    if not donors:
        return None
    state_service = CompensationStateService(session)
    if await state_service.is_excluded_kmg_well(well):
        return None
    donor = donors[0]
    state = await state_service.load([well], now=utc_now())
    pairs = CompensationRecommendationRepository(session)
    open_pairs = await pairs.list_open_by_donor_ids([donor.id])
    latest = await pairs.list_latest_by_donor_ids([donor.id])
    pair = open_pairs[0] if open_pairs else None
    loss_well = (
        await WellRepository(session=session).get_by_id(id_=pair.loss_well_id)
        if pair is not None
        else None
    )
    return {
        "status": donor_status(pair, latest.get(donor.id)),
        "loss_well": loss_well.name if loss_well else None,
        "step_percent": donor.step_percent,
        "gain": donor.gain,
        "risk": donor.risk,
        "unavailable": donor_unavailable_reason(state, well.id),
    }


type Loader = Callable[[AsyncSession, Well], Awaitable[Any]]

# Поле DossierData -> загрузчик раздела.
LOADERS: dict[str, Loader] = {
    "card": _load_card,
    "place": _load_place,
    "lift": _load_lift,
    "station_type": _load_station_type,
    "abai_status": _load_abai_status,
    "incidents": _load_incidents,
    "incident_counts": _load_incident_counts,
    "conclusion": _load_conclusion,
    "repairs": _load_repairs,
    "mrp": _load_mrp,
    "contour": _load_contour,
    "donor": _load_donor,
}


async def _load_section(
    name: str,
    loader: Loader,
    well: Well,
    limit: asyncio.Semaphore,
) -> Any:  # noqa: ANN401
    async with limit:
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
    limit = asyncio.Semaphore(PARALLEL_SECTIONS)
    results = await asyncio.gather(
        *(_load_section(name, loader, well, limit) for name, loader in LOADERS.items()),
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
