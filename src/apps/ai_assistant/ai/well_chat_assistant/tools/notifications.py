# ruff: noqa: TC001, TC002
"""Tool по уведомлениям стены: что зал видел по скважине за период."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from apps.ai_assistant.ai.well_chat_assistant.tools._common import (
    clamp,
    get_well_id,
    iso,
    local_now,
    short,
)
from apps.notifications.dto.internal.notification import NotificationRowDTO
from apps.notifications.dto.queries.notification import NotificationFilterDTO
from apps.notifications.repositories.notification import NotificationRepository
from shared.database.sql.setup import session_makers

NOTIFICATIONS_DEFAULT_DAYS = 7
NOTIFICATIONS_MAX_DAYS = 90
NOTIFICATIONS_LIMIT = 20
BODY_LIMIT = 200

SEVERITY_RU = {
    "info": "информация",
    "warning": "предупреждение",
    "alarm": "авария",
}


def notification_brief(row: NotificationRowDTO) -> dict[str, Any]:
    return {
        "created_at": iso(row.created_at),
        "event_at": iso(row.event_at),
        "severity": row.severity,
        "severity_ru": SEVERITY_RU.get(row.severity, row.severity),
        "event_code": row.event_code,
        "title": row.title_ru,
        "body": short(row.body_ru, BODY_LIMIT),
        "source_type": row.source_type,
        "viewed": row.viewed_at is not None,
    }


@tool
async def list_well_notifications(
    config: RunnableConfig,
    days: int = NOTIFICATIONS_DEFAULT_DAYS,
) -> dict[str, Any]:
    """Уведомления видеостены по скважине за последние ``days`` суток (по
    умолчанию 7, максимум 90), новые сверху, не больше 20: когда записано
    (created_at) и когда случилось (event_at), уровень — информация,
    предупреждение, авария, заголовок и текст, просмотрено ли на стене.
    У одного эпизода бывает цепочка уведомлений: «падение нагрузки» →
    «возможный обрыв штанг» → «момент восстановился».

    Используй для вопросов «что было по скважине за неделю», «какие тревоги
    видел зал», «когда пришло уведомление».
    """
    well_id = get_well_id(config)
    days = clamp(days, 1, NOTIFICATIONS_MAX_DAYS)
    async with session_makers["app"]() as session:
        rows = await NotificationRepository(session).list_rows(
            NotificationFilterDTO(
                well_id=well_id,
                created_from=local_now() - timedelta(days=days),
                limit=NOTIFICATIONS_LIMIT + 1,
            ),
        )
    return {
        "days": days,
        "has_more": len(rows) > NOTIFICATIONS_LIMIT,
        "notifications": [notification_brief(r) for r in rows[:NOTIFICATIONS_LIMIT]],
    }
