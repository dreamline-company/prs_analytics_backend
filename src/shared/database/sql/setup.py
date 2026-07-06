from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from core.settings import get_settings

settings = get_settings()
AVAILABLE_DB = Literal[
    "app",
    "abai",
    "cm",
    "zhylyoi_telemetry",
    "kainar_telemetry",
    "zhmg_telemetry",
    "dmg_telemetry",
]

is_echo = False
engines: dict[AVAILABLE_DB, AsyncEngine] = {
    "abai": create_async_engine(url=settings.ABAI_ASYNC_DATABASE_URL, echo=is_echo),
    "dmg_telemetry": create_async_engine(
        url=settings.DMG_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "kainar_telemetry": create_async_engine(
        url=settings.KAINAR_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "app": create_async_engine(
        url=settings.APP_ASYNC_DATABASE_URL.get_secret_value(),
        echo=is_echo,
    ),
    "cm": create_async_engine(url=settings.CM_ASYNC_DATABASE_URL, echo=is_echo),
}

session_makers = {
    name: async_sessionmaker(engine, expire_on_commit=False)
    for name, engine in engines.items()
}
