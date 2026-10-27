"""Общее для tools скважинного ассистента: well_id из контекста, время, обрезка.

Текущий ``well_id`` инжектится через ``RunnableConfig.configurable``. Каждый
tool открывает собственную сессию БД и отдаёт короткие высокосигнальные поля:
окно модели небольшое, сырые ряды и большие блобы наружу не идут.
"""

from datetime import date, datetime

from langchain_core.runnables import RunnableConfig

from core.settings import get_settings

# Открытый интервал статуса ABAI тянется до 3333-12-31 — дальше этого года
# конец интервала считаем «по сей день».
OPEN_INTERVAL_YEAR = 3000
# Сколько символов текста (план/факт работ, выводы ИИ) отдавать модели.
TEXT_LIMIT = 300


class WellContextMissingError(RuntimeError):
    """Raised when a tool is invoked without a well bound to the chat."""

    default_message = "well_id is not set for this chat. Ask the user to pick a well."

    def __init__(self) -> None:
        super().__init__(self.default_message)


def get_well_id(config: RunnableConfig) -> int:
    configurable = (config or {}).get("configurable") or {}
    well_id = configurable.get("well_id")
    if not isinstance(well_id, int):
        raise WellContextMissingError
    return well_id


def local_now() -> datetime:
    """Наивное местное время — шкала СДМО, ремонтов и статусов ABAI."""
    return datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)


def iso(value: datetime | date | None) -> str | None:
    return value.isoformat() if value is not None else None


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(value, high))


def short(text: str | None, limit: int = TEXT_LIMIT) -> str | None:
    if text is None or len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def rounded(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)
