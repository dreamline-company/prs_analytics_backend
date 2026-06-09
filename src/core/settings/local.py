from core.settings.base import LOG_LEVEL_TYPE, Settings


class LocalSettings(Settings):
    LOG_LEVEL: LOG_LEVEL_TYPE = "DEBUG"
