# ruff: noqa: TC002, TC003
"""Tools exposed to the PRS chat assistant.

The current ``repair_id`` is injected via ``RunnableConfig.configurable`` and
read by every tool that needs it. Each tool opens its own DB session so tool
calls don't share transactional state and can be safely parallelised by the
agent.

Design principle to keep token usage low:

- List/overview tools return short, high-signal fields only (IDs, dates,
  short names, AI verdict summary). Large blobs (raw AI JSON, long text,
  file contents) are hidden behind narrow "get_..._details" tools that the
  agent calls only when the user drills into a specific item.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.files.repositories.file import FileRepository
from apps.org.repositories.brigade import UniqueBrigadeRepository
from apps.org.repositories.ngdu import NGDURepository
from apps.repairs.dto.queries.timeline import GetRepairTimelineQuery
from apps.repairs.models.reports import RepairSummary
from apps.repairs.models.transport import RepairTransport
from apps.repairs.repositories.ai_results import (
    RepairAIAnalysisRepository,
    RepairDynamogramAIResultRepository,
    RepairSPOAIResultRepository,
)
from apps.repairs.repositories.analytics import (
    RepairAnalyticsBrigadeErrorScreenRepository,
    RepairAnalyticsDynamogramRepository,
    RepairAnalyticsRepository,
    RepairAnalyticsSPORepository,
)
from apps.repairs.repositories.brigade import RepairBrigadeRepository
from apps.repairs.repositories.docs import RepairDocRepository
from apps.repairs.repositories.repair import RepairRepository, RepairTypeRepository
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.repairs.use_cases.get_repair_timeline import GetRepairTimelineUseCase
from apps.wells.models.spo import SPO
from apps.wells.repositories.dynamogram import DynamogramRepository
from apps.wells.repositories.spo import SPORepository
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers
from shared.integrations.cm.repositories.brigade_error_screens import (
    CMBrigadeErrorScreenRepository,
)
from shared.repository.sqlalchemy import QuerySpec


class RepairContextMissingError(RuntimeError):
    """Raised when a tool is invoked without a repair bound to the chat."""

    default_message = (
        "repair_id is not set for this chat. Ask the user to pick a repair."
    )

    def __init__(self) -> None:
        super().__init__(self.default_message)


def _get_repair_id(config: RunnableConfig) -> int:
    configurable = (config or {}).get("configurable") or {}
    repair_id = configurable.get("repair_id")
    if not isinstance(repair_id, int):
        raise RepairContextMissingError
    return repair_id


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt is not None else None


def _ai_status(result: Any) -> dict[str, Any] | None:  # noqa: ANN401
    if result is None:
        return None
    return {
        "id": result.id,
        "status": result.status,
        "model_name": result.model_name,
        "prompt_version": result.prompt_version,
        "processed_at": _iso(result.processed_at),
        "has_verdict": bool(result.result),
        "error": result.error,
    }


async def _get_analytics_id(session: AsyncSession, repair_id: int) -> int | None:
    analytics = await RepairAnalyticsRepository(session=session).get_by_repair_id(
        repair_id,
    )
    return analytics.id if analytics else None


@tool
async def get_repair_overview(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает краткую сводку по текущему ремонту: скважина, тип, планируемые
    и выполненные работы, время начала/окончания, признак активности.

    Всегда вызывайте этот tool первым, чтобы понять о каком ремонте речь.
    """
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        repair = await RepairRepository(session=session).get_by_id(repair_id)
        if repair is None:
            return {"error": "repair_not_found", "repair_id": repair_id}
        well = await WellRepository(session=session).get_by_abai_id(
            repair.abai_well_id,
        )
        repair_type = await RepairTypeRepository(session=session).get_by_abai_id(
            repair.repair_type_id,
        )
        return {
            "repair_id": repair.id,
            "abai_repair_id": repair.abai_id,
            "well": {
                "id": well.id if well else None,
                "abai_id": repair.abai_well_id,
                "name": well.name if well else None,
            },
            "repair_type": {
                "id": repair_type.id if repair_type else None,
                "name_ru": repair_type.name_ru if repair_type else None,
                "name_ru_short": (repair_type.name_ru_short if repair_type else None),
            },
            "work_list": repair.work_list,
            "work_plan": repair.work_plan,
            "start_time": _iso(repair.start_time),
            "end_time": _iso(repair.end_time),
            "is_active": repair.end_time is None,
        }


@tool
async def get_repair_brigade(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает бригаду, назначенную на текущий ремонт: id, название,
    НГДУ (id/название)."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        link = await RepairBrigadeRepository(session=session).get_by_repair_id(
            repair_id,
        )
        if link is None:
            return {"repair_id": repair_id, "brigade": None}
        brigade = await UniqueBrigadeRepository(session=session).get_by_id(
            link.brigade_id,
        )
        if brigade is None:
            return {
                "repair_id": repair_id,
                "brigade": {"id": link.brigade_id, "name": None},
            }
        ngdu = await NGDURepository(session=session).get_by_id(brigade.ngdu_id)
        return {
            "repair_id": repair_id,
            "brigade": {
                "id": brigade.id,
                "name": brigade.name,
                "ngdu": {
                    "id": ngdu.id if ngdu else brigade.ngdu_id,
                    "name": ngdu.name if ngdu else None,
                },
            },
        }


@tool
async def list_repair_summaries(config: RunnableConfig) -> list[dict[str, Any]]:
    """Список суточных сводок ПРС по ремонту: id, дата, номер бригады,
    тип насоса, номер смены, техника, устройство.

    Детали смены (shift_details) не возвращаются, чтобы экономить токены —
    используйте get_repair_summary_details для получения полного отчёта."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        summaries = await RepairSummaryRepository(session=session).list_by_repair_id(
            repair_id,
        )
        return [
            {
                "id": s.id,
                "date": s.date.isoformat(),
                "well_id": s.well_id,
                "second_well_id": s.second_well_id,
                "brigade_number": s.brigade_number,
                "pump_type": s.pump_type,
                "shift_type_number": s.shift_type_number,
                "car": s.car,
                "device_number": s.device_number,
                "shift_details_count": (len(s.shift_details) if s.shift_details else 0),
            }
            for s in summaries
        ]


@tool
async def get_repair_summary_details(
    summary_id: int,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Полные детали одной суточной сводки, включая пошаговые записи смены
    (shift_details). Вызывайте только когда пользователь спрашивает про
    конкретный день или содержание смены."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        summary = await RepairSummaryRepository(session=session).get_one(
            QuerySpec(filters=(RepairSummary.id == summary_id,)),
        )
        if summary is None or summary.repair_id != repair_id:
            return {"error": "summary_not_found_or_foreign", "summary_id": summary_id}
        return {
            "id": summary.id,
            "date": summary.date.isoformat(),
            "repair_id": summary.repair_id,
            "well_id": summary.well_id,
            "second_well_id": summary.second_well_id,
            "brigade_number": summary.brigade_number,
            "pump_type": summary.pump_type,
            "shift_type_number": summary.shift_type_number,
            "car": summary.car,
            "device_number": summary.device_number,
            "shift_details": summary.shift_details,
        }


@tool
async def list_repair_dynamograms(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает пару динамограмм ремонта (до/после): id, snapshot_time,
    file_id, наличие AI-анализа.

    Сами картинки/данные динамограмм не возвращаются — для AI-вердикта
    используйте get_dynamogram_ai_analysis, для доступа к файлу — get_file."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        analytics_id = await _get_analytics_id(session, repair_id)
        if analytics_id is None:
            return {"before": None, "after": None}
        link = await RepairAnalyticsDynamogramRepository(
            session=session,
        ).get_by_analytics_id(analytics_id)
        if link is None:
            return {"before": None, "after": None}
        ids = [
            i
            for i in (link.dynamogram_before_id, link.dynamogram_after_id)
            if i is not None
        ]
        dyns = await DynamogramRepository(session=session).list_by_ids(ids)
        by_id = {d.id: d for d in dyns}
        ai_results = await RepairDynamogramAIResultRepository(
            session=session,
        ).list_by_dynamogram_ids(ids)
        ai_by_id = {r.dynamogram_id: r for r in ai_results}

        def brief(dyn_id: int | None) -> dict[str, Any] | None:
            if dyn_id is None or dyn_id not in by_id:
                return None
            d = by_id[dyn_id]
            return {
                "id": d.id,
                "well_id": d.well_id,
                "snapshot_time": _iso(d.snapshot_time),
                "file_id": d.file_id,
                "ai": _ai_status(ai_by_id.get(dyn_id)),
            }

        return {
            "before": brief(link.dynamogram_before_id),
            "after": brief(link.dynamogram_after_id),
        }


@tool
async def get_dynamogram_ai_analysis(
    dynamogram_id: int,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Полный AI-вердикт по одной динамограмме (raw + структурированный
    result). Тяжёлый ответ — вызывайте только когда пользователь просит
    расшифровку/интерпретацию."""
    _get_repair_id(config)
    async with session_makers["app"]() as session:
        ai = await RepairDynamogramAIResultRepository(
            session=session,
        ).get_by_dynamogram_id(dynamogram_id)
        if ai is None:
            return {
                "error": "ai_analysis_not_found",
                "dynamogram_id": dynamogram_id,
            }
        return {
            "id": ai.id,
            "dynamogram_id": ai.dynamogram_id,
            "status": ai.status,
            "model_name": ai.model_name,
            "prompt_version": ai.prompt_version,
            "processed_at": _iso(ai.processed_at),
            "error": ai.error,
            "result": ai.result,
        }


@tool
async def list_repair_spos(config: RunnableConfig) -> list[dict[str, Any]]:
    """Список СПО, привязанных к ремонту через analytics_spo: id, snapshot_time,
    file_id, chart_file_id, notes_file_id, наличие AI-анализа.

    Для AI-разбора используйте get_spo_ai_analysis, для файла — get_file."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        analytics_id = await _get_analytics_id(session, repair_id)
        if analytics_id is None:
            return []
        link = await RepairAnalyticsSPORepository(
            session=session,
        ).get_by_analytics_id(analytics_id)
        if link is None:
            return []
        spo = await SPORepository(session=session).get_one(
            QuerySpec(filters=(SPO.id == link.spo_id,)),
        )
        if spo is None:
            return []
        ai_results = await RepairSPOAIResultRepository(
            session=session,
        ).list_by_spo_ids([spo.id])
        ai = ai_results[0] if ai_results else None
        return [
            {
                "id": spo.id,
                "well_id": spo.well_id,
                "snapshot_time": _iso(spo.snapshot_time),
                "file_id": spo.file_id,
                "chart_file_id": spo.chart_file_id,
                "notes_file_id": spo.notes_file_id,
                "ai": _ai_status(ai),
            },
        ]


@tool
async def get_spo_ai_analysis(
    spo_id: int,
    config: RunnableConfig,
) -> dict[str, Any]:
    """Полный AI-вердикт по одной СПО. Тяжёлый ответ — вызывайте только
    при явном запросе на расшифровку/интерпретацию."""
    _get_repair_id(config)
    async with session_makers["app"]() as session:
        ai = await RepairSPOAIResultRepository(session=session).get_by_spo_id(spo_id)
        if ai is None:
            return {"error": "ai_analysis_not_found", "spo_id": spo_id}
        return {
            "id": ai.id,
            "spo_id": ai.spo_id,
            "status": ai.status,
            "model_name": ai.model_name,
            "prompt_version": ai.prompt_version,
            "processed_at": _iso(ai.processed_at),
            "error": ai.error,
            "result": ai.result,
        }


@tool
async def get_repair_docs(config: RunnableConfig) -> dict[str, Any]:
    """Возвращает документы ремонта: id и даты создания файлов ПОР и Акта ПРС."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        doc = await RepairDocRepository(session=session).get_by_repair_id(repair_id)
        if doc is None:
            return {"por": None, "act": None}
        file_repo = FileRepository(session=session)
        por_file = (
            await file_repo.get_by_id(doc.por_file_id)
            if doc.por_file_id is not None
            else None
        )
        act_file = (
            await file_repo.get_by_id(doc.act_file_id)
            if doc.act_file_id is not None
            else None
        )
        return {
            "por": (
                {"file_id": por_file.id, "created_at": _iso(por_file.created_at)}
                if por_file
                else None
            ),
            "act": (
                {"file_id": act_file.id, "created_at": _iso(act_file.created_at)}
                if act_file
                else None
            ),
        }


@tool
async def get_repair_overall_ai_analysis(
    config: RunnableConfig,
) -> dict[str, Any]:
    """Итоговый AI-вердикт по всему ремонту (aggregated overall analysis):
    статус, verdict (raw + parsed), время расчёта."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        analytics_id = await _get_analytics_id(session, repair_id)
        if analytics_id is None:
            return {"error": "analytics_not_found", "repair_id": repair_id}
        overall = await RepairAIAnalysisRepository(
            session=session,
        ).get_by_analytics_id(analytics_id)
        if overall is None:
            return {"error": "ai_analysis_not_found", "repair_id": repair_id}
        result = overall.result or {}
        return {
            "id": overall.id,
            "status": overall.status,
            "model_name": overall.model_name,
            "prompt_version": overall.prompt_version,
            "processed_at": _iso(overall.processed_at),
            "error": overall.error,
            "verdict_parsed": (
                result.get("parsed") if isinstance(result, dict) else None
            ),
            "verdict_raw": (result.get("raw") if isinstance(result, dict) else None),
        }


@tool
async def get_repair_timeline(config: RunnableConfig) -> list[dict[str, Any]]:
    """9-событийная хронология ремонта: ПОР, Динамограмма до, Начало СПО,
    Спецтехника, Сводка, Конец СПО, Запуск, Выход на тех. режим, Акт ПРС.
    Возвращает даты (или null, если событие не произошло)."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        use_case = GetRepairTimelineUseCase(
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
        )
        timeline = await use_case.execute(GetRepairTimelineQuery(repair_id=repair_id))
        return [
            {
                "code": e.code,
                "label": e.label,
                "date": e.date.isoformat() if e.date is not None else None,
            }
            for e in timeline.events
        ]


@tool
async def list_repair_transport(config: RunnableConfig) -> list[dict[str, Any]]:
    """Список записей спецтехники (транспорта), задействованной в ремонте:
    номер операции, тип работ, статус, плановые/фактические даты, техника."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as session:
        rows = (
            (
                await session.execute(
                    select(RepairTransport)
                    .where(RepairTransport.repair_id == repair_id)
                    .order_by(RepairTransport.actual_date.asc().nulls_last()),
                )
            )
            .scalars()
            .all()
        )
        return [
            {
                "id": r.id,
                "operation_number": r.operation_number,
                "operation_code": r.operation_code,
                "work_type": r.work_type,
                "status_name": r.status_name,
                "closure_status": r.closure_status,
                "planned_start_at": _iso(r.planned_start_at),
                "planned_end_at": _iso(r.planned_end_at),
                "actual_date": _iso(r.actual_date),
                "vehicle_number": r.vehicle_number,
                "vehicle_class_name": r.vehicle_class_name,
                "department": r.department,
                "engine_hours": r.engine_hours,
                "mileage": r.mileage,
            }
            for r in rows
        ]


@tool
async def list_brigade_error_screens(config: RunnableConfig) -> list[dict[str, Any]]:
    """Список экранов ошибок бригады, привязанных к ремонту (данные из
    CM-системы). Возвращает id, brigade_id, timestamp, описание, путь к
    скриншоту и статус обработки."""
    repair_id = _get_repair_id(config)
    async with session_makers["app"]() as app_session:
        analytics_id = await _get_analytics_id(app_session, repair_id)
        if analytics_id is None:
            return []
        links = await RepairAnalyticsBrigadeErrorScreenRepository(
            session=app_session,
        ).list_by_analytics_id(analytics_id)
        cm_screen_ids = [link.cm_screen_id for link in links]
        if not cm_screen_ids:
            return []
    async with session_makers["cm"]() as cm_session:
        screens = await CMBrigadeErrorScreenRepository(
            session=cm_session,
        ).list_by_ids(cm_screen_ids)
        return [
            {
                "id": s.id,
                "brigade_id": s.brigade_id,
                "timestamp": _iso(s.timestamp),
                "description": s.description,
                "screen_path": s.screen,
                "is_processed": s.is_processed,
            }
            for s in screens
        ]


@tool
async def get_file(file_id: int, config: RunnableConfig) -> dict[str, Any]:
    """Возвращает метаинформацию о файле (путь в хранилище, дата создания)
    для доступа к динамограмме, СПО, ПОР или Акту.

    Используйте file_id, полученный из других tools (get_repair_docs,
    list_repair_dynamograms, list_repair_spos)."""
    _get_repair_id(config)
    async with session_makers["app"]() as session:
        file = await FileRepository(session=session).get_by_id(file_id)
        if file is None:
            return {"error": "file_not_found", "file_id": file_id}
        return {
            "id": file.id,
            "path": file.file,
            "created_at": _iso(file.created_at),
        }


CHAT_ASSISTANT_TOOLS = [
    get_repair_overview,
    get_repair_brigade,
    list_repair_summaries,
    get_repair_summary_details,
    list_repair_dynamograms,
    get_dynamogram_ai_analysis,
    list_repair_spos,
    get_spo_ai_analysis,
    get_repair_docs,
    get_repair_overall_ai_analysis,
    get_repair_timeline,
    list_repair_transport,
    list_brigade_error_screens,
    get_file,
]
