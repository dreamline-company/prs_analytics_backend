# ruff: noqa: TC001, TC002
"""Tools по ремонтам скважины: список, МРП, разбор одного ремонта."""

from __future__ import annotations

from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    clamp,
    get_well_id,
    iso,
    short,
)
from apps.files.repositories.file import FileRepository
from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.repairs.dto.queries.kpi import GetRepairKPIQuery
from apps.repairs.dto.queries.mrp import GetWellMrpQuery
from apps.repairs.dto.queries.timeline import GetRepairTimelineQuery
from apps.repairs.models.repair import Repair
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.kpi import RepairKPIRepository
from apps.repairs.repositories.repair import RepairRepository, RepairTypeRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.use_cases.get_repair_kpi import (
    GetRepairKPIUseCase,
    RepairKPINotFoundError,
)
from apps.repairs.use_cases.get_repair_timeline import GetRepairTimelineUseCase
from apps.repairs.use_cases.get_well_mrp import GetWellMrpUseCase
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers

REPAIRS_DEFAULT_LIMIT = 10
# В списке ремонтов — только начало перечня работ, полный текст в разборе.
LIST_TEXT_LIMIT = 120
REPAIRS_COLUMNS = ("repair_id", "repair_type", "category", "start", "end", "work_list")
REPAIRS_MAX_LIMIT = 20
MRP_RECENT_REPAIRS = 8
# Сколько пунктов списков ИИ-разбора (риски, рекомендации …) отдавать.
AI_LIST_LIMIT = 5

# Категория ремонта ABAI (well_workover.repair_type).
REPAIR_CATEGORY = {1: "КРС", 3: "ПРС"}


def repair_brief(repair: Repair, type_name: str | None) -> dict[str, Any]:
    return {
        "repair_id": repair.id,
        "repair_type": type_name,
        "category": REPAIR_CATEGORY.get(repair.abai_repair_type_id),
        "start_time": iso(repair.start_time),
        "end_time": None if repair.is_open else iso(repair.end_time),
        "is_active": repair.is_open,
        "work_plan": short(repair.work_plan),
        "work_list": short(repair.work_list),
    }


async def list_repairs_with_type(
    session: AsyncSession,
    abai_well_id: int,
) -> list[tuple[Repair, str | None]]:
    """Ремонты скважины (без удалённых в ABAI), свежие сверху."""
    rows = await RepairRepository(session).list_with_type_by_well_abai_ids(
        [abai_well_id],
    )
    return sorted(rows, key=lambda row: (row[0].start_time, row[0].id), reverse=True)


@tool
async def list_repairs(
    config: RunnableConfig,
    limit: int = REPAIRS_DEFAULT_LIMIT,
) -> dict[str, Any]:
    """Последние ремонты скважины (по умолчанию 10, максимум 20), свежие
    сверху: таблица ``rows`` по столбцам ``columns`` — repair_id, вид работ,
    категория ПРС/КРС, начало, конец (null — ремонт идёт), начало перечня
    выполненных работ. План и факт работ целиком, KPI и вердикт ИИ — в
    get_repair_analysis по repair_id отсюда."""
    well_id = get_well_id(config)
    limit = clamp(limit, 1, REPAIRS_MAX_LIMIT)
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return {"error": "well_not_found", "well_id": well_id}
        rows = await list_repairs_with_type(session, well.abai_id)
    return {
        "total": len(rows),
        "columns": REPAIRS_COLUMNS,
        "rows": [
            [
                repair.id,
                name,
                REPAIR_CATEGORY.get(repair.abai_repair_type_id),
                iso(repair.start_time),
                None if repair.is_open else iso(repair.end_time),
                short(repair.work_list, LIST_TEXT_LIMIT),
            ]
            for repair, name in rows[:limit]
        ],
    }


@tool
async def get_well_mrp(config: RunnableConfig) -> dict[str, Any]:
    """Межремонтный период (МРП) скважины: сколько она проработала между
    соседними ремонтами. Средний МРП по истории, МРП до и после каждого
    ремонта и признак, сократился ли он после ремонта. Готовую текстовую
    интерпретацию в поле ``interpretation`` перескажи пользователю, числа не
    пересчитывай."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        mrp = await GetWellMrpUseCase(
            repair_repository=RepairRepository(session=session),
            well_repository=WellRepository(session=session),
        ).execute(GetWellMrpQuery(well_id=well_id))
    return {
        "avg_mrp_days": mrp.avg_mrp_days,
        "intervals_count": mrp.intervals_count,
        "repairs_count": mrp.repairs_count,
        "worse_count": mrp.worse_count,
        "interpretation": mrp.interpretation,
        "recent_repairs": [
            {
                "repair_id": item.repair_id,
                "start_time": iso(item.start_time),
                "repair_type": item.repair_type_name_ru,
                "mrp_before_days": item.mrp_before_days,
                "mrp_after_days": item.mrp_after_days,
                "verdict": item.verdict,
            }
            for item in mrp.repairs[:MRP_RECENT_REPAIRS]
        ],
    }


def _parsed(result: dict | None) -> dict[str, Any] | None:
    """Разобранный JSON ответа ИИ (``{"raw", "parsed"}``) или None."""
    if not isinstance(result, dict):
        return None
    parsed = result.get("parsed")
    return parsed if isinstance(parsed, dict) else None


def _items(value: Any) -> list[str]:  # noqa: ANN401
    if not isinstance(value, list):
        return []
    return [short(str(item), 200) for item in value[:AI_LIST_LIMIT]]


async def _brigade_name(session: AsyncSession, repair_id: int) -> str | None:
    link = await RepairBrigadeRepository(session=session).get_by_repair_id(repair_id)
    if link is None:
        return None
    brigade = await UniqueBrigadeRepository(session=session).get_by_id(
        link.brigade_id,
    )
    return brigade.name if brigade else None


async def _timeline(session: AsyncSession, repair_id: int) -> list[dict[str, Any]]:
    timeline = await GetRepairTimelineUseCase(
        repair_repository=RepairRepository(session=session),
        analytics_repository=RepairAnalyticsRepository(session=session),
        analytics_dynamogram_repository=RepairAnalyticsDynamogramRepository(
            session=session,
        ),
        dynamogram_repository=DynamogramRepository(session=session),
        spo_repository=SPORepository(session=session),
        doc_repository=RepairDocRepository(session=session),
        summary_repository=RepairSummaryRepository(session=session),
        file_repository=FileRepository(session=session),
    ).execute(GetRepairTimelineQuery(repair_id=repair_id))
    return [{"event": e.label, "date": iso(e.date)} for e in timeline.events]


async def _kpi(session: AsyncSession, repair_id: int) -> dict[str, Any] | None:
    try:
        kpi = await GetRepairKPIUseCase(
            analytics_repository=RepairAnalyticsRepository(session=session),
            kpi_repository=RepairKPIRepository(session=session),
        ).execute(GetRepairKPIQuery(repair_id=repair_id))
    except RepairKPINotFoundError:
        return None
    return kpi.metrics.model_dump(mode="json")


async def _overall_ai(session: AsyncSession, analytics_id: int) -> dict | None:
    overall = await RepairAIAnalysisRepository(session=session).get_by_analytics_id(
        analytics_id,
    )
    parsed = _parsed(overall.result) if overall else None
    if parsed is None:
        return None
    return {
        "score": parsed.get("score"),
        "good_points": _items(parsed.get("good_points")),
        "risks_and_issues": _items(parsed.get("risks_and_issues")),
        "attention_points": _items(parsed.get("attention_points")),
        "recommendations": _items(parsed.get("recommendations")),
    }


async def _dynamograms_ai(session: AsyncSession, analytics_id: int) -> dict | None:
    link = await RepairAnalyticsDynamogramRepository(
        session=session,
    ).get_by_analytics_id(analytics_id)
    if link is None:
        return None
    ids = [
        item
        for item in (link.dynamogram_before_id, link.dynamogram_after_id)
        if item is not None
    ]
    snapshots = {
        dyn.id: dyn.snapshot_time
        for dyn in await DynamogramRepository(session=session).list_by_ids(ids)
    }
    results = {
        result.dynamogram_id: _parsed(result.result)
        for result in await RepairDynamogramAIResultRepository(
            session=session,
        ).list_by_dynamogram_ids(ids)
    }

    def brief(dyn_id: int | None) -> dict[str, Any] | None:
        if dyn_id is None:
            return None
        parsed = results.get(dyn_id) or {}
        return {
            "snapshot_time": iso(snapshots.get(dyn_id)),
            "condition": parsed.get("condition"),
            "fill_pct": parsed.get("fill_pct"),
            "pump_efficiency_pct": parsed.get("pump_efficiency_pct"),
            "score": parsed.get("score"),
            "issues": _items(parsed.get("issues")),
            "verdict": short(parsed.get("verdict")),
        }

    return {
        "before": brief(link.dynamogram_before_id),
        "after": brief(link.dynamogram_after_id),
    }


async def _spo_ai(session: AsyncSession, analytics_id: int) -> dict | None:
    link = await RepairAnalyticsSPORepository(session=session).get_by_analytics_id(
        analytics_id,
    )
    if link is None:
        return None
    result = await RepairSPOAIResultRepository(session=session).get_by_spo_id(
        link.spo_id,
    )
    parsed = _parsed(result.result) if result else None
    if parsed is None:
        return {"spo_id": link.spo_id, "ai": None}
    hook = parsed.get("hook_weight") or {}
    events = parsed.get("events")
    return {
        "spo_id": link.spo_id,
        "summary": short(parsed.get("summary")),
        "hook_weight_min_t": hook.get("min_t") if isinstance(hook, dict) else None,
        "hook_weight_max_t": hook.get("max_t") if isinstance(hook, dict) else None,
        "events_count": len(events) if isinstance(events, list) else 0,
        "attention_points": _items(parsed.get("attention_points")),
    }


@tool
async def get_repair_analysis(
    repair_id: int,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Разбор одного ремонта этой скважины (repair_id — из list_repairs или
    get_well_mrp): вид работ, даты, бригада, хронология (ПОР, СПО, запуск,
    выход на режим, акт), KPI ремонта, общий вердикт ИИ (оценка, риски,
    рекомендации), ИИ-оценка динамограмм до/после и краткий разбор СПО.
    Выводы ИИ носят рекомендательный характер."""
    well_id = get_well_id(config)
    async with session_makers["app"]() as session:
        repair = await RepairRepository(session=session).get_by_id(repair_id)
        if repair is None:
            return {"error": "repair_not_found", "repair_id": repair_id}
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None or repair.abai_well_id != well.abai_id:
            return {"error": "repair_of_another_well", "repair_id": repair_id}
        repair_type = await RepairTypeRepository(session=session).get_by_abai_id(
            repair.repair_type_id,
        )
        result: dict[str, Any] = {
            **repair_brief(repair, repair_type.name_ru if repair_type else None),
            "brigade": await _brigade_name(session, repair.id),
        }
        analytics = await RepairAnalyticsRepository(session=session).get_by_repair_id(
            repair.id,
        )
        if analytics is None:
            result["analytics"] = None
            return result
        result.update(
            timeline=await _timeline(session, repair.id),
            kpi=await _kpi(session, repair.id),
            overall_ai=await _overall_ai(session, analytics.id),
            dynamograms_ai=await _dynamograms_ai(session, analytics.id),
            spo_ai=await _spo_ai(session, analytics.id),
        )
        return result
