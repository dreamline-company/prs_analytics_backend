"""Отсечка отсчётов «из будущего» для загрузок СДМО и ЦИТС.

Курсор обеих загрузок — максимальное время замера у нас. Одна строка с датой
из будущего (опечатка в ЦИТС, скачок часов станции) сдвинула бы курсор вперёд,
и настоящие отсчёты до этой даты не загрузились бы никогда. Поэтому отсчёты
позже ``future_limit()`` не грузятся и не участвуют в курсоре. Допуск в сутки
пропускает станции, чьи часы просто спешат.
"""

from datetime import datetime, timedelta

from core.settings import get_settings

settings = get_settings()

FUTURE_TOLERANCE = timedelta(days=1)


def future_limit() -> datetime:
    """Местное наивное время, позже которого отсчёт считается ошибочным."""
    return datetime.now(tz=settings.ZONE_INFO).replace(tzinfo=None) + FUTURE_TOLERANCE
