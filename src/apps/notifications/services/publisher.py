"""Сигнал хабам после коммита: в канал Redis уходят id новых строк.

Сигнал не обязателен для доставки — потерянный подберёт опрос хаба (окно
15 мин). Поэтому сбой Redis таску не роняет: пишем предупреждение и выходим.

Канал — ``<APP_NAME>:<ENV>:notifications``, сообщение — JSON:
``{"notification_ids": [...]}`` или ``{"view_ids": [...]}``.
"""

import json
from collections.abc import Iterable

import redis.asyncio as redis

from apps.notifications.constants import redis_channel
from core import get_logger
from core.settings import get_settings

logger = get_logger(__name__)

KEY_NOTIFICATIONS = "notification_ids"
KEY_VIEWS = "view_ids"
_TIMEOUT_SECONDS = 3


async def publish_notifications(ids: Iterable[int | None]) -> None:
    """id уведомлений после коммита; ``None`` (дубль, выключено) отбрасываются."""
    await _publish(KEY_NOTIFICATIONS, ids)


async def publish_views(view_ids: Iterable[int | None]) -> None:
    """id отметок «просмотрено» после коммита."""
    await _publish(KEY_VIEWS, view_ids)


async def _publish(key: str, ids: Iterable[int | None]) -> None:
    clean = sorted({i for i in ids if i is not None})
    if not clean:
        return
    message = json.dumps({key: clean})
    client = redis.Redis.from_url(
        get_settings().APP_REDIS_DB_URL,
        socket_connect_timeout=_TIMEOUT_SECONDS,
        socket_timeout=_TIMEOUT_SECONDS,
    )
    try:
        await client.publish(redis_channel(), message)
    except (redis.RedisError, OSError):
        logger.warning(
            "Notifications signal skipped (Redis unavailable): %s; "
            "hub poll will deliver",
            message,
        )
    finally:
        try:
            await client.aclose()
        except (redis.RedisError, OSError):
            logger.debug("Redis client close failed", exc_info=True)
