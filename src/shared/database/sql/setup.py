from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from core.settings import get_settings

settings = get_settings()
AVAILABLE_DB = Literal["abai", "telemetry", "app"]

is_echo = settings.LOG_LEVEL == "DEBUG"
engines: dict[AVAILABLE_DB, AsyncEngine] = {
    "abai": create_async_engine(url=settings.ABAI_ASYNC_DATABASE_URL, echo=is_echo),
    "telemetry": create_async_engine(
        url=settings.TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "app": create_async_engine(url=settings.APP_ASYNC_DATABASE_URL, echo=is_echo),
}

session_makers = {
    name: async_sessionmaker(engine, expire_on_commit=False)
    for name, engine in engines.items()
}
