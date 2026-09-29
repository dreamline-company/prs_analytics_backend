"""Подбор доноров под потери — чистая логика, без базы.

Правила (решения владельца, 29.09.2026):

- донор ищется только на месторождении потери;
- потери обходятся от больших к меньшим, доноры добираются, пока потеря не
  покрыта; донор закрепляется только за одной потерей;
- порядок доноров: сначала низкий и средний риск по расстоянию, доноры с
  высоким риском — только когда на месторождении кончились остальные;
- риск — по ступени прироста скорости, которую автор пула выбирал по запасу
  столба жидкости: 15 % — низкий, 10 % — средний, меньше — высокий.
"""

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.compensation.constants import (
    CLOSE_DONOR_UNAVAILABLE,
    CLOSE_LOSS_RESOLVED,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)

_RISK_BY_STEP = {15: RISK_LOW, 10: RISK_MEDIUM}

_EARTH_RADIUS_M = 6_371_000

type Point = tuple[float, float]  # (широта, долгота), WGS 84


def risk_for_step(step_percent: int) -> str:
    return _RISK_BY_STEP.get(step_percent, RISK_HIGH)


def target_speed(speed: float, step_percent: int) -> float:
    """Скорость после разгона на ступень, с точностью до десятых."""
    return round(speed * (1 + step_percent / 100), 1)


def distance_m(a: Point | None, b: Point | None) -> int | None:
    """Расстояние по поверхности Земли, м; None — у одной из скважин нет координат."""
    if a is None or b is None:
        return None
    lat1, lon1, lat2, lon2 = map(math.radians, (*a, *b))
    h = (
        math.sin((lat2 - lat1) / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin((lon2 - lon1) / 2) ** 2
    )
    return round(2 * _EARTH_RADIUS_M * math.asin(math.sqrt(h)))


def covered(loss: float, gains: Iterable[float]) -> float:
    """Сколько потери закрыто донорами: их прирост, но не больше самой потери."""
    return min(sum(gains), loss)


@dataclass(frozen=True, slots=True)
class LossInput:
    well_id: int
    field: str  # префикс месторождения (VMB в VMB_0220)
    value: float  # план Qн, т/сут
    covered: float  # уже закрыто открытыми парами
    point: Point | None


@dataclass(frozen=True, slots=True)
class DonorInput:
    donor_id: int
    field: str
    gain: float
    risk: str
    point: Point | None


@dataclass(frozen=True, slots=True)
class NewPair:
    loss_well_id: int
    donor_id: int
    distance_m: int | None


def allocate(
    losses: Sequence[LossInput],
    free_donors: Sequence[DonorInput],
    *,
    excluded: frozenset[tuple[int, int]] = frozenset(),
) -> list[NewPair]:
    """Закрепить свободных доноров за непокрытыми частями потерь.

    ``excluded`` — пары (скважина потери, донор), которые технолог отклонил:
    этого донора этой потере больше не предлагаем.
    """
    used: set[int] = set()
    pairs: list[NewPair] = []
    for loss in sorted(losses, key=lambda item: (-item.value, item.well_id)):
        remaining = loss.value - loss.covered
        if remaining <= 0:
            continue
        candidates = [
            donor
            for donor in free_donors
            if donor.donor_id not in used
            and donor.field == loss.field
            and (loss.well_id, donor.donor_id) not in excluded
        ]
        ranked = sorted(
            ((donor, distance_m(loss.point, donor.point)) for donor in candidates),
            key=lambda item: (
                item[0].risk == RISK_HIGH,
                item[1] is None,
                item[1] or 0,
                item[0].donor_id,
            ),
        )
        for donor, distance in ranked:
            if remaining <= 0:
                break
            used.add(donor.donor_id)
            pairs.append(NewPair(loss.well_id, donor.donor_id, distance))
            remaining -= donor.gain
    return pairs


@dataclass(frozen=True, slots=True)
class OpenPair:
    pair_id: int
    loss_well_id: int
    donor_well_id: int


def pairs_to_close(
    open_pairs: Sequence[OpenPair],
    *,
    stopped_well_ids: set[int],
    available_donor_well_ids: set[int],
) -> list[tuple[int, str]]:
    """Какие пары закрыть и почему: потеря снята или донор недоступен."""
    result = []
    for pair in open_pairs:
        if pair.loss_well_id not in stopped_well_ids:
            result.append((pair.pair_id, CLOSE_LOSS_RESOLVED))
        elif pair.donor_well_id not in available_donor_well_ids:
            result.append((pair.pair_id, CLOSE_DONOR_UNAVAILABLE))
    return result
