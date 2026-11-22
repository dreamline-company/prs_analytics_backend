"""Блок «Пользователь открыл уведомление» для системного промпта.

Чат привязали к скважине из карточки уведомления (``notification_id``):
модели надо знать, о каком событии спрашивают. Блок короткий — около 300
токенов: окно модели 16k, досье уже занимает свою часть. Эпизод-источник
модель найдёт в досье и в ``get_incidents``; блок говорит, на чём
сосредоточиться.
"""

from collections.abc import Sequence
from datetime import datetime

from apps.ai_assistant.ai.well_chat_assistant.tools._common import short
from apps.ai_assistant.ai.well_chat_assistant.tools.notifications import SEVERITY_RU
from apps.notifications.dto.responses.notification import (
    NotificationCardDTO,
    NotificationChainItemDTO,
)
from apps.notifications.repositories.notification import NotificationRepository
from apps.notifications.services.items import NotificationItemBuilder
from apps.notifications.use_cases.get_notification_card import (
    GetNotificationCardUseCase,
)
from core import get_logger

logger = get_logger(__name__)

BODY_LIMIT = 300
FACTS_LIMIT = 8
FACT_LIMIT = 80
CHAIN_LIMIT = 5
TITLE_LIMIT = 120
# ~300 токенов при 2 символах на токен — с запасом на шапку.
BLOCK_LIMIT = 900

SOURCE_RU = {
    "detector_incident": "эпизод детектора",
    "repair": "ремонт",
    "state_run": "прогон состояния скважин",
}


def _dt(value: datetime) -> str:
    return value.strftime("%d.%m.%Y %H:%M")


def _severity(value: str) -> str:
    return SEVERITY_RU.get(value, value)


def _chain_line(item: NotificationChainItemDTO, current_id: int) -> str:
    mark = " ← это уведомление" if item.id == current_id else ""
    title = short(item.title_ru, TITLE_LIMIT)
    return f"  - {_dt(item.created_at)} {_severity(item.severity)}: {title}{mark}"


def _chain(card: NotificationCardDTO) -> Sequence[NotificationChainItemDTO]:
    """Последние звенья цепочки, текущее — всегда в выборке."""
    if len(card.chain) <= CHAIN_LIMIT:
        return card.chain
    tail = list(card.chain[-CHAIN_LIMIT:])
    if all(item.id != card.id for item in tail):
        current = next(item for item in card.chain if item.id == card.id)
        tail = [current, *tail[1:]]
    return tail


def render_notification_block(card: NotificationCardDTO) -> str:
    lines = [
        "Пользователь открыл это уведомление и спрашивает о нём: в первую очередь "
        "объясни, что случилось и почему.",
        f"- «{short(card.title_ru, TITLE_LIMIT)}» — {_severity(card.severity)}",
        f"- Событие: {_dt(card.event_at)}; записано: {_dt(card.created_at)}",
    ]
    if card.body_ru:
        lines.append(f"- Текст: {short(card.body_ru, BODY_LIMIT)}")
    if card.facts:
        facts = "; ".join(
            short(f"{fact.label_ru} — {fact.value_ru}", FACT_LIMIT) or ""
            for fact in card.facts[:FACTS_LIMIT]
        )
        lines.append(f"- Факты на момент уведомления: {facts}")
    if card.source_type is not None:
        source = SOURCE_RU.get(card.source_type, card.source_type)
        lines.append(f"- Источник: {source}, id {card.source_id}")
    if len(card.chain) > 1:
        lines.append("- Цепочка уведомлений этого источника:")
        lines.extend(_chain_line(item, card.id) for item in _chain(card))
    text = "\n".join(lines)
    return short(text, BLOCK_LIMIT) or ""


class NotificationBlockProvider:
    """Блок уведомления для вопроса; ``None`` — не нашлось или чужая скважина."""

    async def get(self, notification_id: int, well_id: int) -> str | None:
        from shared.database.sql.setup import session_makers  # noqa: PLC0415

        try:
            async with session_makers["app"]() as session:
                card = await GetNotificationCardUseCase(
                    notification_repository=NotificationRepository(session),
                    item_builder=NotificationItemBuilder.for_session(session),
                ).execute(notification_id)
        except Exception:
            logger.exception("Notification block failed: id=%s", notification_id)
            return None
        if card is None:
            logger.warning("Notification %s not found for chat", notification_id)
            return None
        if card.well is not None and card.well.id != well_id:
            logger.warning(
                "Notification %s belongs to well %s, chat is bound to %s",
                notification_id,
                card.well.id,
                well_id,
            )
            return None
        return render_notification_block(card)


notification_block_provider = NotificationBlockProvider()
