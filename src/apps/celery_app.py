import asyncio
from collections.abc import Coroutine
from typing import Any

from celery import Celery

from core.settings import get_settings

settings = get_settings()
broker_url = settings.CELERY_BROKER_REDIS_DB_URL
celery_app = Celery(
    "my_app",
    broker=settings.CELERY_BROKER_REDIS_DB_URL,
    backend=settings.CELERY_BACKEND_REDIS_DB_URL,
)


def run_async[T](coro: Coroutine[Any, Any, T]) -> T:
    """asyncio.run для celery-тасок: перед закрытием цикла гасит пулы БД.

    Prefork-процесс воркера живёт дольше одного event loop: каждый запуск
    таски создаёт новый цикл, а глобальные движки из
    ``shared.database.sql.setup`` кэшируют соединения, привязанные к циклу
    создания. Без dispose следующая таска в том же процессе достаёт из пула
    соединение уже закрытого цикла и падает с "attached to a different loop".
    """

    async def _run() -> T:
        # Ленивый импорт: клиентам celery_app, которым нужен только
        # send_task, движки БД не нужны.
        from shared.database.sql.setup import engines  # noqa: PLC0415

        try:
            return await coro
        finally:
            for engine in engines.values():
                await engine.dispose()

    return asyncio.run(_run())
