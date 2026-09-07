"""Замок «одна загрузка SDMO на НГДУ» в Redis.

Инкремент по beat каждые 5 минут и разовая bulk-заливка истории одного НГДУ
не должны идти одновременно: оба COPY'ят строки станций от курсора и второй
упирается в уникальный индекс ``(station_id, sdmo_id)``. Замок с TTL: упавший
процесс не удержит его дольше лимита. Bulk держит замок часами и продлевает
его по ходу; инкремент, увидев замок, просто пропускает прогон.
"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

import redis.asyncio as redis

from core import get_logger
from core.settings import get_settings

logger = get_logger(__name__)

# Инкремент: совпадает с task_time_limit celery — дольше таска всё равно не живёт.
LOCK_TTL_SEC = 30 * 60
# Bulk: продлевается каждые LOCK_REFRESH_SEC, поэтому TTL короткий — после
# гибели процесса инкремент вернётся через час, а не через сутки.
BULK_LOCK_TTL_SEC = 60 * 60
LOCK_REFRESH_SEC = 10 * 60

# Redis недоступен (standalone-запуск без брокера): замок считается взятым,
# наложение контролирует оператор.
NO_REDIS_TOKEN = "no-redis"  # noqa: S105 — маркер, не секрет

# Снимать/продлевать только свой замок: чужой (следующего прогона после
# истечения TTL) трогать нельзя.
_RELEASE_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('del', KEYS[1])
end
return 0
"""
_REFRESH_SCRIPT = """
if redis.call('get', KEYS[1]) == ARGV[1] then
    return redis.call('expire', KEYS[1], ARGV[2])
end
return 0
"""


def lock_key(abai_ngdu_id: int) -> str:
    return f"sdmo:incremental:{abai_ngdu_id}"


def _client() -> redis.Redis:
    return redis.Redis.from_url(get_settings().APP_REDIS_DB_URL)


async def acquire_ngdu_load_lock(
    abai_ngdu_id: int,
    *,
    ttl_sec: int = LOCK_TTL_SEC,
) -> str | None:
    """Токен замка или ``None``, если загрузка этого НГДУ уже идёт."""
    token = uuid4().hex
    client = _client()
    try:
        acquired = await client.set(lock_key(abai_ngdu_id), token, nx=True, ex=ttl_sec)
    except redis.RedisError:
        logger.warning(
            "Redis unavailable; SDMO load for NGDU %s runs unlocked",
            abai_ngdu_id,
        )
        return NO_REDIS_TOKEN
    finally:
        await client.aclose()
    return token if acquired else None


async def refresh_ngdu_load_lock(
    abai_ngdu_id: int,
    token: str,
    *,
    ttl_sec: int,
) -> bool:
    """Продлить свой замок; ``False`` — замок уже не наш (истёк и перехвачен)."""
    if token == NO_REDIS_TOKEN:
        return True
    client = _client()
    try:
        return bool(
            await client.eval(
                _REFRESH_SCRIPT,
                1,
                lock_key(abai_ngdu_id),
                token,
                ttl_sec,
            ),
        )
    except redis.RedisError:
        logger.warning(
            "Redis unavailable; lock for NGDU %s not refreshed",
            abai_ngdu_id,
        )
        return True
    finally:
        await client.aclose()


async def release_ngdu_load_lock(abai_ngdu_id: int, token: str) -> None:
    if token == NO_REDIS_TOKEN:
        return
    client = _client()
    try:
        await client.eval(_RELEASE_SCRIPT, 1, lock_key(abai_ngdu_id), token)
    except redis.RedisError:
        logger.warning(
            "Redis unavailable; lock for NGDU %s left to expire",
            abai_ngdu_id,
        )
    finally:
        await client.aclose()


async def _keepalive(
    abai_ngdu_id: int,
    token: str,
    ttl_sec: int,
    every_sec: float,
) -> None:
    while True:
        await asyncio.sleep(every_sec)
        if not await refresh_ngdu_load_lock(abai_ngdu_id, token, ttl_sec=ttl_sec):
            logger.error(
                "SDMO load lock for NGDU %s lost (expired and taken over)",
                abai_ngdu_id,
            )
            return


@asynccontextmanager
async def ngdu_load_lock(
    abai_ngdu_id: int,
    *,
    ttl_sec: int = LOCK_TTL_SEC,
    refresh_sec: float = LOCK_REFRESH_SEC,
) -> AsyncIterator[bool]:
    """Yield ``True``, если замок взят, ``False`` — прогон этого НГДУ уже идёт.

    Пока тело контекста работает, замок продлевается фоновой задачей: первый
    инкремент нового НГДУ — это вся история одним процессом, он идёт дольше
    любого разумного TTL, а истёкший замок пустил бы параллельный прогон.
    """
    token = await acquire_ngdu_load_lock(abai_ngdu_id, ttl_sec=ttl_sec)
    if token is None:
        yield False
        return
    keepalive = asyncio.create_task(
        _keepalive(abai_ngdu_id, token, ttl_sec, refresh_sec),
    )
    try:
        yield True
    finally:
        keepalive.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await keepalive
        await release_ngdu_load_lock(abai_ngdu_id, token)
