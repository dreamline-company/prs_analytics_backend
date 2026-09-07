from typing import Literal

from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from core.settings import get_settings
from shared.constants.ngdu import AbaiNGDUIDsEnum

settings = get_settings()
AVAILABLE_DB = Literal[
    "app",
    "abai",
    "cm",
    "zhylmg_telemetry",
    "kainar_telemetry",
    "zhmg_telemetry",
    "dmg_telemetry",
]


def sdmo_engine_key(ngdu: AbaiNGDUIDsEnum) -> str:
    """Ключ движка/сессии SDMO-базы НГДУ: ``sdmo_kmg``, ``sdmo_dmg``, ..."""
    return f"sdmo_{ngdu.name.lower()}"


is_echo = False
engines: dict[str, AsyncEngine] = {
    "abai": create_async_engine(url=settings.ABAI_ASYNC_DATABASE_URL, echo=is_echo),
    "dmg_telemetry": create_async_engine(
        url=settings.DMG_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "kainar_telemetry": create_async_engine(
        url=settings.KAINAR_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "zhmg_telemetry": create_async_engine(
        url=settings.ZHMG_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "zhylmg_telemetry": create_async_engine(
        url=settings.ZHYLMG_TELEMETRY_ASYNC_DATABASE_URL,
        echo=is_echo,
    ),
    "app": create_async_engine(
        url=settings.APP_ASYNC_DATABASE_URL.get_secret_value(),
        echo=is_echo,
    ),
    "cm": create_async_engine(url=settings.CM_ASYNC_DATABASE_URL, echo=is_echo),
}
# SDMO: по движку на НГДУ с настроенным URL (см. Settings.sdmo_database_urls).
for _ngdu, _url in settings.sdmo_database_urls().items():
    engines[sdmo_engine_key(_ngdu)] = create_async_engine(url=_url, echo=is_echo)

session_makers = {
    name: async_sessionmaker(engine, expire_on_commit=False)
    for name, engine in engines.items()
}
