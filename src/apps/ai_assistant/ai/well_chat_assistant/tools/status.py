# ruff: noqa: TC002, TC003
"""Tool по статусам ABAI: в работе / простой с причиной / периодическая."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from sqlalchemy.ext.asyncio import AsyncSession

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    OPEN_INTERVAL_YEAR,
    clamp,
    get_well_id,
    iso,
    local_now,
)
from apps.wells.repositories import WellStatusRepository
from apps.wells.repositories.well import WellRepository
from shared.database.sql.setup import session_makers

STATUS_DEFAULT_DAYS = 30
STATUS_MAX_DAYS = 90
STATUS_LIMIT = 30


def status_brief(row: Sequence[Any]) -> dict[str, Any]:
    """Строка интервала (abai_well_id, dbeg, dend, код, название, причина)."""
    _, dbeg, dend, code, name, reason = row
    return {
        "status_code": code,
        "status": name,
        "reason": reason,
        "since": iso(dbeg),
        "until": None if dend.year >= OPEN_INTERVAL_YEAR else iso(dend),
    }


async def list_status_rows(
    session: AsyncSession,
    abai_well_id: int,
    *,
    since: datetime,
    until: datetime,
) -> Sequence[Sequence[Any]]:
    """Интервалы статусов ABAI. Время в копии местное, как и у ремонтов."""
    return await WellStatusRepository(session).list_intervals_by_abai_well_ids(
        [abai_well_id],
        since=since,
        until=until,
    )


@tool
async def get_abai_status_history(
    config: RunnableConfig,
    days: int = STATUS_DEFAULT_DAYS,
) -> dict[str, Any]:
    """Статусы скважины в ABAI за последние ``days`` суток (по умолчанию 30,
    максимум 90): «В работе», «В простое» с причиной (нет подачи, ПРС,
    отключение электроэнергии …), периодическая эксплуатация. Интервалы с
    началом и концом; until=null — статус действует сейчас.

    Используй для вопросов «стоит ли скважина», «почему стояла», «когда
    запустили», а также чтобы отличить плановую остановку от отказа.
    """
    well_id = get_well_id(config)
    days = clamp(days, 1, STATUS_MAX_DAYS)
    now = local_now()
    async with session_makers["app"]() as session:
        well = await WellRepository(session=session).get_by_id(id_=well_id)
        if well is None:
            return {"error": "well_not_found", "well_id": well_id}
        rows = await list_status_rows(
            session,
            well.abai_id,
            since=now - timedelta(days=days),
            until=now,
        )
    intervals = [status_brief(row) for row in rows][-STATUS_LIMIT:]
    return {"days": days, "intervals": intervals}
