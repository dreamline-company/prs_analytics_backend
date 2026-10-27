# ruff: noqa: TC001, TC002
"""Tools контура компенсации: потеря стоящей скважины, скважина как донор,
доноры месторождения.

Данные — те же use case, что у экранов контура, чтобы статусы и цифры в чате
совпадали с фронтом. Пул доноров — разовый расчёт автора (Excel), скважины вне
него донорами не бывают.
"""

from __future__ import annotations

from typing import Any, Literal

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ai_assistant.ai.well_chat_assistant.tools._common import get_well_id
from apps.compensation.constants import STOP_IDLE
from apps.compensation.dto.internal.compensation import (
    CompensationDonorDTO,
    CompensationDonorsDTO,
)
from apps.compensation.dto.queries.compensation import (
    GetCompensationContourQuery,
    ListCompensationDonorsQuery,
)
from apps.compensation.repositories import CompensationRecommendationRepository
from apps.compensation.services.state import CompensationStateService, utc_now
from apps.compensation.use_cases.get_contour import GetCompensationContourUseCase
from apps.compensation.use_cases.list_donors import ListCompensationDonorsUseCase
from apps.org.repositories.oil_field import OilFieldRepository
from apps.org.services import well_name_prefix
from apps.wells.models.well import Well
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers

# У крупной потери доноров бывает под три десятка — модели хватит главных.
DONORS_LIMIT = 10
FIELD_DONORS_LIMIT = 15
FIELD_DONORS_COLUMNS = (
    "well",
    "status",
    "risk",
    "gain_t_day",
    "speed_from",
    "speed_to",
    "speed_units",
    "step_percent",
    "qn_t_day",
    "water_cut_pct",
    "loss_well",
)
DONOR_STATUS_RU = {
    "pending": "на согласовании",
    "accepted": "принят технологом",
    "applied": "разгон применён",
    "rejected": "отклонён технологом",
    "reserve": "в резерве (не закреплён)",
}

RISK_RU = {"low": "низкий", "medium": "средний", "high": "высокий"}

DonorStatusFilter = Literal["pending", "accepted", "applied", "rejected", "reserve"]


async def field_donors(
    session: AsyncSession,
    well: Well,
    *,
    status: str | None = None,
) -> CompensationDonorsDTO | None:
    """Пул доноров месторождения скважины; None — скважина вне контура.

    Один префикс может числиться за несколькими НГДУ, поэтому месторождение
    берётся то, в чей круг скважин (с правилом «у КМГ только VMB») она входит.
    """
    prefix = well_name_prefix(well.name)
    if prefix is None:
        return None
    state_service = CompensationStateService(session)
    for oil_field in await OilFieldRepository(session).list_by_prefix(prefix):
        scope = await state_service.scope_wells(oil_field.ngdu_id, oil_field.id)
        if any(item.id == well.id for item in scope):
            return await ListCompensationDonorsUseCase(session).execute(
                ListCompensationDonorsQuery(
                    ngdu_id=oil_field.ngdu_id,
                    oil_field_id=oil_field.id,
                    status=status,
                ),
            )
    return None


def _speed(item: CompensationDonorDTO) -> dict[str, Any]:
    return {
        "from": item.speed.from_,
        "to": item.speed.to,
        "step_percent": item.speed.step_percent,
        "units": item.speed.units,
    }


@tool
async def get_compensation_contour(config: RunnableConfig) -> dict[str, Any]:
    """Контур компенсации со стороны стоящей скважины: стоит ли она (state:
    stopped — стоит и потеря считается, no_plan — стоит, но плана по нефти
    нет, working — не стоит), почему стоит, потеря (план Qн, т/сут), сколько
    компенсировано разгоном доноров и какие доноры за ней закреплены: прирост,
    скорость привода было → станет, риск, расстояние, статус пары.
    Если скважина работает — она может быть донором: см. get_donor_role."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        contour = await GetCompensationContourUseCase(session).execute(
            GetCompensationContourQuery(well_id=well_id),
        )
        pairs = await CompensationRecommendationRepository(
            session,
        ).list_open_by_loss_well_ids([well_id])
    speeds = {pair.id: (pair.speed_from, pair.speed_to) for pair in pairs}
    data = contour.model_dump(mode="json")
    donors = data.get("main_donors") or []
    for donor in donors:
        speed_from, speed_to = speeds.get(donor["recommendation_id"], (None, None))
        donor["speed_from"] = speed_from
        donor["speed_to"] = speed_to
    data["main_donors"] = donors[:DONORS_LIMIT]
    data["donors_total"] = len(donors)
    return data


@tool
async def get_donor_role(config: RunnableConfig) -> dict[str, Any]:
    """Текущая скважина как донор: есть ли она в пуле доноров, статус
    (в резерве / на согласовании / принят / применён / отклонён), за какой
    стоящей скважиной закреплена, на сколько разогнать привод (было → станет,
    ступень %), ожидаемый прирост нефти, риск разгона, Qн, обводнённость,
    погружение; может ли она сейчас быть донором (не стоит и без тревоги).
    Прирост сценарный: запас по оборотам привода и пропускная способность ГЗУ
    не проверялись."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return {"error": "well_not_found", "well_id": well_id}
        pool = await field_donors(session, well)
        state = await CompensationStateService(session).load([well], now=utc_now())
    item = next(
        (donor for donor in (pool.items if pool else []) if donor.well.id == well_id),
        None,
    )
    if item is None:
        return {
            "is_donor": False,
            # Вне контура: у Кайнармунайгаза в него входит только VMB.
            "in_contour": pool is not None,
            "pool_calculated_at": (
                pool.summary.pool_calculated_at.isoformat()
                if pool and pool.summary.pool_calculated_at
                else None
            ),
        }
    stop = state.stops.get(well_id)
    unavailable = None
    if stop is not None:
        unavailable = "стоит: " + (
            "простой ABAI" if stop.kind == STOP_IDLE else "идёт ремонт"
        )
    elif well_id in state.alarms:
        unavailable = "по скважине открыта тревога детектора (alarm)"
    return {
        "is_donor": True,
        "status": item.status,
        "status_ru": DONOR_STATUS_RU.get(item.status),
        "assigned_to_loss_well": item.loss_well.well_name if item.loss_well else None,
        "available_now": unavailable is None,
        "unavailable_reason": unavailable,
        "risk": item.risk,
        "risk_ru": RISK_RU.get(item.risk),
        "gain_t_day": item.gain,
        "gain_kind": item.gain_kind,
        "speed": _speed(item),
        "lift_type": item.lift_type,
        "qn_t_day": item.qn,
        "water_cut_pct": item.constraints.water_cut_percent,
        "submergence_m": item.constraints.submergence_m,
        "speed_margin_checked": item.constraints.speed_margin_checked,
        "oil_field": item.oil_field.name,
        "pool_calculated_at": (
            pool.summary.pool_calculated_at.isoformat()
            if pool and pool.summary.pool_calculated_at
            else None
        ),
    }


@tool
async def list_field_donors(
    config: RunnableConfig,
    status: DonorStatusFilter | None = None,
) -> dict[str, Any]:
    """Доноры месторождения текущей скважины: сводка (кандидатов, доступный
    потенциал т/сут, применено, на согласовании, % реализации, дата расчёта
    пула), число доноров по статусам и до 15 доноров с наибольшим приростом
    таблицей ``rows`` по столбцам ``columns``: скважина, статус, риск,
    прирост т/сут, скорость было → станет, ступень %, Qн, обводнённость, за
    какой стоящей скважиной закреплён. ``status`` сужает список: pending —
    на согласовании, accepted, applied, rejected, reserve — свободные."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return {"error": "well_not_found", "well_id": well_id}
        pool = await field_donors(session, well, status=status)
    if pool is None:
        return {"error": "well_out_of_compensation_contour", "well_id": well_id}
    items = pool.items
    return {
        "oil_field": items[0].oil_field.name if items else None,
        "summary": pool.summary.model_dump(mode="json"),
        "counts": pool.counts.model_dump(mode="json"),
        "status_filter": status,
        "shown": min(len(items), FIELD_DONORS_LIMIT),
        "total": len(items),
        "columns": FIELD_DONORS_COLUMNS,
        "rows": [
            [
                item.well.well_name,
                item.status,
                item.risk,
                item.gain,
                item.speed.from_,
                item.speed.to,
                item.speed.units,
                item.speed.step_percent,
                item.qn,
                item.constraints.water_cut_percent,
                item.loss_well.well_name if item.loss_well else None,
            ]
            for item in items[:FIELD_DONORS_LIMIT]
        ],
    }
