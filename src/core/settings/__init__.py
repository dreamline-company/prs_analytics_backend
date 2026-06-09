from functools import lru_cache

from core.settings.base import CoreSettings, Settings
from core.settings.dev import DevSettings
from core.settings.local import LocalSettings
from core.settings.prod import ProdSettings

__all__ = ["get_settings"]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    core_settings = CoreSettings()
    match core_settings.ENV:
        case "dev":
            return DevSettings()
        case "prod":
            return ProdSettings()
        case _:
            return LocalSettings()
