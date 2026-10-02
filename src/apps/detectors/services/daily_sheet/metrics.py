"""Статистика по замерам дебита для граф ведомости — чистые функции."""


def ratio(value: float | None, plan: float | None) -> float | None:
    if value is None or plan is None or plan <= 0:
        return None
    return value / plan
