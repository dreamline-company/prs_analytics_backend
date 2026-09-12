"""Замки и debounce на Redis для тасок ремонтов.

Замок с TTL защищает от наложения прогонов (событийный запуск по ремонту и
часовой sweep могут сойтись на одном ремонте). Debounce схлопывает серию
триггеров по одному ремонту в один запуск аналитики: файлы из разных
источников приходят пачкой, а каждый запуск стоит LLM-токенов.

Redis недоступен (standalone-запуск без брокера) — замок считается взятым,
debounce не срабатывает: наложение контролирует оператор.
"""

import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import redis.asyncio as redis

from core import get_logger
from core.settings import get_settings

logger = get_logger(__name__)

NO_REDIS_TOKEN = "no-redis"  # noqa: S105 — маркер, не секрет

_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""


def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().APP_REDIS_DB_URL)


async def acquire_lock(key: str, *, ttl_sec: int) -> str | None:
    """Токен замка или ``None``, если ключ уже занят."""
    token = uuid4().hex
    client = _client()
    try:
        acquired = await client.set(key, token, nx=True, ex=ttl_sec)
    except redis.RedisError:
        logger.warning("Redis unavailable; %s runs unlocked", key)
        return NO_REDIS_TOKEN
    finally:
        await client.aclose()
    return token if acquired else None


async def release_lock(key: str, token: str) -> None:
    if token == NO_REDIS_TOKEN:
        return
    client = _client()
    try:
        await client.eval(_RELEASE_SCRIPT, 1, key, token)
    except redis.RedisError:
        logger.warning("Redis unavailable; lock %s left to expire", key)
    finally:
        await client.aclose()


@asynccontextmanager
async def redis_lock(key: str, *, ttl_sec: int) -> AsyncIterator[bool]:
    """Yield ``True``, если замок взят, ``False`` — прогон уже идёт."""
    token = await acquire_lock(key, ttl_sec=ttl_sec)
    if token is None:
        yield False
        return
    try:
        yield True
    finally:
        with contextlib.suppress(Exception):
            await release_lock(key, token)


async def debounce(key: str, *, window_sec: int) -> bool:
    """``True`` — первый вызов в окне, действие нужно выполнить.

    Повторные вызовы внутри окна получают ``False``. Без Redis каждый вызов
    считается первым: лучше лишний запуск, чем потерянный.
    """
    client = _client()
    try:
        return bool(await client.set(key, "1", nx=True, ex=window_sec))
    except redis.RedisError:
        logger.warning("Redis unavailable; debounce %s skipped", key)
        return True
    finally:
        await client.aclose()
