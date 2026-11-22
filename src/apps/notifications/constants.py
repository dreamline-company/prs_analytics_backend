"""Общие константы уведомлений: уровни, канал Redis, окна хаба."""

from datetime import datetime, timedelta
from typing import Literal

from core.settings import get_settings

SEVERITY_INFO = "info"
SEVERITY_WARNING = "warning"
SEVERITY_ALARM = "alarm"
SEVERITIES: tuple[str, ...] = (SEVERITY_INFO, SEVERITY_WARNING, SEVERITY_ALARM)
Severity = Literal["info", "warning", "alarm"]

# Канал pub/sub: после коммита в него уходят id новых уведомлений и отметок
# «просмотрено». Номер БД Redis каналу не важен, а пространство имён каналов
# у Redis общее — поэтому в имени приложение и окружение: чужой стенд на том
# же Redis не разбудит стену прода.
REDIS_CHANNEL_SUFFIX = "notifications"

# Верхняя граница id (BIGINT): больше — 422/1007 до похода в БД.
MAX_ID = 2**63 - 1

# Эмиттер не пишет события старше этого: пересборки истории (restage, recheck,
# backfill) не должны будить стену.
MAX_EVENT_AGE = timedelta(hours=24)

# Хаб: подстраховочный опрос журнала, его окно, ping сокетов. Окно считается
# по created_at (время INSERT), а видна строка после коммита: производитель
# обязан коммитить вскоре после emit — транзакция заметно короче окна.
POLL_INTERVAL_SECONDS = 10.0
POLL_WINDOW = timedelta(minutes=15)
PING_INTERVAL_SECONDS = 25.0

# Снимок при подключении: без after_id — последние N, и общий потолок.
SNAPSHOT_LATEST = 50
SNAPSHOT_MAX = 200

# История: размер страницы.
HISTORY_DEFAULT_LIMIT = 50
HISTORY_MAX_LIMIT = 200

EXPLOITATION_BY_NAME_RU = {"ЭВН": "evn", "ШГН": "shgn"}


def redis_channel() -> str:
    settings = get_settings()
    return f"{settings.APP_NAME}:{settings.ENV}:{REDIS_CHANNEL_SUFFIX}"


def local_now() -> datetime:
    """Местное наивное время: БД в UTC, ``now()`` сдвинул бы на 5 часов."""
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


def exploitation_code(name_ru: str | None) -> Literal["evn", "shgn"] | None:
    """Способ эксплуатации для фронта — тем же правилом, что строка матрицы."""
    if name_ru is None:
        return None
    return EXPLOITATION_BY_NAME_RU.get(name_ru.strip().upper())  # type: ignore[return-value]
