"""Статистика по замерам дебита для граф ведомости — чистые функции.

Точки — ``(время, значение)``; ``None`` в значении означает пропуск замера.
"""

from collections.abc import Sequence
from datetime import datetime, timedelta

Point = tuple[datetime, float | None]


def median(values: Sequence[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def window_values(
    points: Sequence[Point],
    *,
    end: datetime,
    days: int,
) -> list[float]:
    """Непустые значения за [end − days, end)."""
    start = end - timedelta(days=days)
    return [value for at, value in points if value is not None and start <= at < end]


def window_medians(
    points: Sequence[Point],
    *,
    end: datetime,
    days: int,
) -> tuple[float | None, float | None]:
    """(медиана последнего окна, медиана окна перед ним) — «дебит снизился с … до …»."""
    recent = median(window_values(points, end=end, days=days))
    previous = median(
        window_values(points, end=end - timedelta(days=days), days=days),
    )
    return recent, previous


def edge_means(
    points: Sequence[Point],
    *,
    end: datetime,
    span_days: int,
    window_days: int,
) -> tuple[float | None, float | None]:
    """(среднее первого окна периода, среднее последнего) — тренд обводнённости."""
    span_start = end - timedelta(days=span_days)
    first = window_values(
        points,
        end=span_start + timedelta(days=window_days),
        days=window_days,
    )
    last = window_values(points, end=end, days=window_days)
    return _mean(first), _mean(last)


def ratio(value: float | None, plan: float | None) -> float | None:
    if value is None or plan is None or plan <= 0:
        return None
    return value / plan


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None
