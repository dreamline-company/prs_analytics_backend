from core.settings.base import LOG_LEVEL_TYPE, Settings


class DevSettings(Settings):
    LOG_LEVEL: LOG_LEVEL_TYPE = "DEBUG"
