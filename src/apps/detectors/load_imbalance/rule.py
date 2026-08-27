"""Правило R9: перекос нагрузки на ходе станка-качалки.

K = |P5| / P95 по суточному распределению момента на валу. P95 — характерный
рабочий пик (ход вверх, поднимаем штанги со столбом жидкости), P5 —
характерный обратный пик (ход вниз, противовесы раскручивают привод). Когда
полезная нагрузка уходит, баланс ломается с двух сторон разом: рабочие пики
падают (нечего поднимать), обратные растут (противовесы перевешивают). K
чувствителен вдвойне — числитель растёт, знаменатель падает.

Три ветви, а не один порог:

* абсолютная — K >= K_ALERT, универсальная граница «так здоровые не работают».
  Глушится на скважинах с хронически высоким собственным фоном (P90 выше
  BASE_P90_LIMIT), иначе они дают поток пустых тревог;
* относительная — K >= K_DEGRADE и K >= REL_FACTOR x своего фонового P90.
  Ловит тихие скважины, у которых утечка началась с 0.04 и дошла до 0.15: до
  абсолютного порога далеко, а рост в разы уже очевиден;
* потеря нагрузки — P95 ниже LOAD_LOSS_FRAC фоновой медианы P95. Страховочная:
  когда нагрузка рухнула целиком, дробь K вырождается и вести себя может как
  угодно, поэтому диагноз ставится по факту, а не по балансу.

Фон строго каузальный — только прошлое, никакого подглядывания в будущее.
Сутки, уже вызвавшие тревогу, из фона последующих суток исключаются: иначе
медленная деградация неделя за неделей втягивается в собственный фон, порог
ползёт вслед за ней и правило глушит само себя.
"""

import math
from collections.abc import Sequence
from datetime import timedelta

from apps.detectors.load_imbalance import config
from apps.detectors.load_imbalance.dto.internal.day import (
    BRANCH_ABSOLUTE,
    BRANCH_LOAD_LOSS,
    BRANCH_RELATIVE,
    STATE_ALERT,
    STATE_CLEAN,
    STATE_GREY,
    STATE_UNDETERMINED,
    Baseline,
    DayAggregate,
    DayVerdict,
)


def quantile(sorted_values: Sequence[float], q: float) -> float:
    """Перцентиль с линейной интерполяцией между порядковыми статистиками.

    Та же схема, что в скриптах прогона (и в ``numpy`` по умолчанию), — чтобы
    наши числа сходились с валидационным отчётом.

    Args:
        sorted_values: Непустая отсортированная по возрастанию последовательность.
        q: Доля в диапазоне 0..1.

    Returns:
        Значение перцентиля.
    """
    position = (len(sorted_values) - 1) * q
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return sorted_values[low]
    weight = position - low
    return sorted_values[low] + weight * (sorted_values[high] - sorted_values[low])


def is_valid(day: DayAggregate) -> bool:
    """Годятся ли сутки для оценки: набрано достаточно снимков."""
    return day.n_samples >= config.DAY_MIN_SAMPLES


def build_baseline(history: Sequence[DayAggregate]) -> Baseline | None:
    """Собрать фон по валидным суткам окна.

    ``None`` — если валидных суток с определённым K меньше ``MIN_BASE_DAYS``:
    сравнивать не с чем, правило на таких сутках не запускается вовсе.
    """
    ks = sorted(day.k for day in history if day.k is not None)
    if len(ks) < config.MIN_BASE_DAYS:
        return None
    p95s = sorted(day.p95 for day in history if day.p95 > 0)
    return Baseline(
        n_days=len(ks),
        k_p90=quantile(ks, config.BASE_K_PERCENTILE),
        p95_median=quantile(p95s, config.P_MEDIAN) if p95s else None,
    )


def _branches(day: DayAggregate, baseline: Baseline) -> tuple[str, ...]:
    """Какие ветви правила сработали на этих сутках."""
    hits: list[str] = []
    k = day.k
    if (
        k is not None
        and baseline.k_p90 <= config.BASE_P90_LIMIT
        and k >= config.K_ALERT
    ):
        hits.append(BRANCH_ABSOLUTE)
    if (
        k is not None
        and k >= config.K_DEGRADE
        and k >= config.REL_FACTOR * baseline.k_p90
    ):
        hits.append(BRANCH_RELATIVE)
    if (
        baseline.p95_median is not None
        and day.p95 < config.LOAD_LOSS_FRAC * baseline.p95_median
    ):
        hits.append(BRANCH_LOAD_LOSS)
    return tuple(hits)


def evaluate_day(day: DayAggregate, baseline: Baseline | None) -> DayVerdict:
    """Классифицировать одни сутки относительно фона.

    Исходов четыре, а не два: «нет сработки» само по себе не означает норму —
    в него иначе попадают и серая зона деградации, и сутки, которые вообще
    нельзя было оценить.
    """
    branches: tuple[str, ...] = ()
    if not is_valid(day) or baseline is None:
        state = STATE_UNDETERMINED
    else:
        branches = _branches(day, baseline)
        if branches:
            state = STATE_ALERT
        elif day.k is not None and day.k < config.K_DEGRADE:
            state = STATE_CLEAN
        else:
            state = STATE_GREY

    return DayVerdict(
        day=day.day,
        state=state,
        n_samples=day.n_samples,
        p5=day.p5,
        p50=day.p50,
        p95=day.p95,
        k=day.k,
        branches=branches,
        baseline=baseline,
    )


def evaluate_series(days: Sequence[DayAggregate]) -> list[DayVerdict]:
    """Каузальный прогон ряда суток: фон каждых суток — только их прошлое.

    Воспроизводит то, что система знала бы в тот день вживую, поэтому
    полученные точность и полнота честно переносятся на прод.

    Args:
        days: Суточные агрегаты станции, любой порядок.

    Returns:
        Вердикты в хронологическом порядке, по одному на каждые входные сутки.
    """
    ordered = sorted(days, key=lambda day: day.day)
    verdicts: list[DayVerdict] = []
    # Валидные сутки, не вызвавшие тревогу, — материал для будущего фона.
    history: list[DayAggregate] = []

    for day in ordered:
        window_start = day.day - timedelta(days=config.BASE_WINDOW_DAYS)
        window = [d for d in history if window_start <= d.day < day.day]
        verdict = evaluate_day(day, build_baseline(window))
        verdicts.append(verdict)
        if is_valid(day) and verdict.state != STATE_ALERT:
            history.append(day)

    return verdicts
