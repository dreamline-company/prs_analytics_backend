"""Подбор доноров под потери — чистая логика, без базы.

Правила (решения владельца, 29.09.2026):

- донор ищется только на месторождении потери;
- потери обходятся от больших к меньшим, доноры добираются, пока потеря не
  покрыта; донор закрепляется только за одной потерей;
- порядок доноров: сначала низкий и средний риск по расстоянию, доноры с
  высоким риском — только когда на месторождении кончились остальные;
- риск — по ступени прироста скорости, которую автор пула выбирал по запасу
  столба жидкости: 15 % — низкий, 10 % — средний, меньше — высокий.

С 27.10.2026 за потерей не больше ``MAX_DONORS_PER_LOSS`` доноров: пул
меньше суммы потерь, и без лимита его целиком забирала крупнейшая. Лишние
пары «на согласовании» закрываются, их доноры уходят следующим потерям.

С 31.10.2026 крупная потеря перехватывает доноров у меньших
(docs/compensation_preemption_plan.md): иначе потеря, вставшая позже, оставалась
без доноров при разобранном пуле. Порядок поиска для потери: свободные низкого и
среднего риска, перехват таких же, свободные высокого, перехват высокого.
Перехват — только пары «на согласовании», у потери меньше в ``PREEMPT_RATIO``
раза (без плана — всегда), если текущая стоит не меньше ``PREEMPT_AFTER`` и
покрытие НГДУ не падает; жертвы — сначала самая мелкая потеря, у неё ближайший
донор. Донор, взятый в прогоне, сразу числится за новой потерей.
"""

import math
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from apps.compensation.constants import (
    CLOSE_DONOR_LIMIT,
    CLOSE_DONOR_UNAVAILABLE,
    CLOSE_LOSS_RESOLVED,
    MAX_DONORS_PER_LOSS,
    PREEMPT_RATIO,
    RECOMMENDATION_PENDING,
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


def _donor_rank(
    risk: str,
    distance: int | None,
    tiebreak: int,
) -> tuple[bool, bool, int, int]:
    """Кто из доноров потери лучше: низкий/средний риск, затем ближе."""
    return (risk == RISK_HIGH, distance is None, distance or 0, tiebreak)


@dataclass(frozen=True, slots=True)
class LossInput:
    well_id: int
    field: str  # префикс месторождения (VMB в VMB_0220)
    value: float  # план Qн, т/сут
    point: Point | None
    # Стоит не меньше PREEMPT_AFTER — может перехватывать доноров у меньших.
    can_preempt: bool = False


@dataclass(frozen=True, slots=True)
class DonorInput:
    donor_id: int
    field: str
    gain: float
    risk: str
    point: Point | None


@dataclass(frozen=True, slots=True)
class HeldDonor:
    """Донор открытой пары: занимает лимит и покрытие своей потери."""

    pair_id: int
    loss_well_id: int
    donor: DonorInput
    pending: bool  # «на согласовании» — можно перехватить


@dataclass(frozen=True, slots=True)
class NewPair:
    loss_well_id: int
    donor_id: int
    distance_m: int | None
    preempted_pair_id: int | None = None  # донор перехвачен из этой пары


@dataclass(frozen=True, slots=True)
class Allocation:
    pairs: list[NewPair]

    @property
    def preempted(self) -> list[int]:
        """Пары, закрываемые с причиной «перехвачен»."""
        return [
            pair.preempted_pair_id
            for pair in self.pairs
            if pair.preempted_pair_id is not None
        ]


# Погрешность сравнения тонн: приросты и планы — дроби с двумя знаками.
_EPS = 1e-9


class _Allocator:
    """Состояние одного подбора: кто чей донор, покрытие и число доноров."""

    def __init__(  # noqa: PLR0913
        self,
        losses: Sequence[LossInput],
        free_donors: Sequence[DonorInput],
        held: Sequence[HeldDonor],
        *,
        excluded: frozenset[tuple[int, int]],
        max_donors: int,
        preempt_ratio: float,
    ) -> None:
        self.values = {loss.well_id: loss.value for loss in losses}
        self.free = list(free_donors)
        self.excluded = excluded
        self.max_donors = max_donors
        self.preempt_ratio = preempt_ratio
        self.used: set[int] = set()  # свободные, взятые в этом прогоне
        self.sums: dict[int, float] = defaultdict(float)
        self.counts: dict[int, int] = defaultdict(int)
        # Можно перехватить: донор → его пара «на согласовании» (пока она цела).
        self.preemptable: dict[int, HeldDonor] = {}
        self.pairs: list[NewPair] = []
        for item in held:
            self.sums[item.loss_well_id] += item.donor.gain
            self.counts[item.loss_well_id] += 1
            if item.pending:
                self.preemptable[item.donor.donor_id] = item

    def run(self, losses: Sequence[LossInput]) -> Allocation:
        for loss in sorted(losses, key=lambda item: (-item.value, item.well_id)):
            for high in (False, True):
                self._take_free(loss, high=high)
                if loss.can_preempt:
                    self._take_held(loss, high=high)
        return Allocation(self.pairs)

    def _remaining(self, loss: LossInput) -> float:
        return loss.value - self.sums[loss.well_id]

    def _open(self, loss: LossInput) -> bool:
        return (
            self._remaining(loss) > _EPS and self.counts[loss.well_id] < self.max_donors
        )

    def _fits(self, loss: LossInput, donor: DonorInput, *, high: bool) -> bool:
        return (
            (donor.risk == RISK_HIGH) == high
            and donor.field == loss.field
            and (loss.well_id, donor.donor_id) not in self.excluded
        )

    def _assign(
        self,
        loss: LossInput,
        donor: DonorInput,
        distance: int | None,
        preempted_pair_id: int | None = None,
    ) -> None:
        self.sums[loss.well_id] += donor.gain
        self.counts[loss.well_id] += 1
        self.pairs.append(
            NewPair(loss.well_id, donor.donor_id, distance, preempted_pair_id),
        )

    def _take_free(self, loss: LossInput, *, high: bool) -> None:
        ranked = sorted(
            (
                (donor, distance_m(loss.point, donor.point))
                for donor in self.free
                if donor.donor_id not in self.used
                and self._fits(loss, donor, high=high)
            ),
            key=lambda item: _donor_rank(item[0].risk, item[1], item[0].donor_id),
        )
        for donor, distance in ranked:
            if not self._open(loss):
                return
            self.used.add(donor.donor_id)
            self._assign(loss, donor, distance)

    def _owner_loses(self, owner_id: int, gain: float) -> float:
        """Сколько потерял бы владелец в покрытии (с потолком своей величины);
        у потери без плана величина 0 — она не теряет ничего."""
        value = self.values.get(owner_id, 0.0)
        before = self.sums[owner_id]
        return min(before, value) - min(before - gain, value)

    def _take_held(self, loss: LossInput, *, high: bool) -> None:
        candidates = [
            (item, distance_m(loss.point, item.donor.point))
            for item in self.preemptable.values()
            if item.loss_well_id != loss.well_id
            and self._fits(loss, item.donor, high=high)
            and self.values.get(item.loss_well_id, 0.0) * self.preempt_ratio
            <= loss.value + _EPS
        ]
        # Жертвы — сначала самая мелкая потеря, у неё ближайший донор.
        candidates.sort(
            key=lambda pair: (
                self.values.get(pair[0].loss_well_id, 0.0),
                pair[1] is None,
                pair[1] or 0,
                pair[0].donor.donor_id,
            ),
        )
        for item, distance in candidates:
            if not self._open(loss):
                return
            gain_here = min(item.donor.gain, self._remaining(loss))
            if gain_here + _EPS < self._owner_loses(item.loss_well_id, item.donor.gain):
                continue  # покрытие НГДУ упало бы
            del self.preemptable[item.donor.donor_id]
            self.sums[item.loss_well_id] -= item.donor.gain
            self.counts[item.loss_well_id] -= 1
            self._assign(loss, item.donor, distance, item.pair_id)


def allocate(  # noqa: PLR0913
    losses: Sequence[LossInput],
    free_donors: Sequence[DonorInput],
    *,
    held: Sequence[HeldDonor] = (),
    excluded: frozenset[tuple[int, int]] = frozenset(),
    max_donors: int = MAX_DONORS_PER_LOSS,
    preempt_ratio: float = PREEMPT_RATIO,
) -> Allocation:
    """Закрепить доноров за непокрытыми частями потерь.

    ``held`` — доноры открытых пар (после закрытия недоступных и сверх
    лимита): они занимают лимит и покрытие своих потерь, а пары «на
    согласовании» могут перехватить потери крупнее. ``excluded`` — пары
    (скважина потери, донор), которые технолог отклонил. Перехват закрывает
    пару владельца (``Allocation.preempted``) — донор числится за новой
    потерей сразу, повторно в этом прогоне его не забирают.
    """
    return _Allocator(
        losses,
        free_donors,
        held,
        excluded=excluded,
        max_donors=max_donors,
        preempt_ratio=preempt_ratio,
    ).run(losses)


@dataclass(frozen=True, slots=True)
class OpenPair:
    pair_id: int
    loss_well_id: int
    donor_well_id: int
    status: str
    risk: str  # риск донора
    distance_m: int | None


def pairs_to_close(
    open_pairs: Sequence[OpenPair],
    *,
    stopped_well_ids: set[int],
    available_donor_well_ids: set[int],
    max_donors: int = MAX_DONORS_PER_LOSS,
) -> list[tuple[int, str]]:
    """Какие пары закрыть и почему: потеря снята, донор недоступен или у
    потери доноров больше лимита.

    Сверх лимита закрываются только пары «на согласовании», худшие по тому же
    порядку, что в ``allocate``; принятые и применённые остаются и занимают
    места лимита первыми.
    """
    result = []
    kept: dict[int, list[OpenPair]] = defaultdict(list)
    for pair in open_pairs:
        if pair.loss_well_id not in stopped_well_ids:
            result.append((pair.pair_id, CLOSE_LOSS_RESOLVED))
        elif pair.donor_well_id not in available_donor_well_ids:
            result.append((pair.pair_id, CLOSE_DONOR_UNAVAILABLE))
        else:
            kept[pair.loss_well_id].append(pair)

    for pairs in kept.values():
        ranked = sorted(
            pairs,
            key=lambda pair: (
                pair.status == RECOMMENDATION_PENDING,
                _donor_rank(pair.risk, pair.distance_m, pair.pair_id),
            ),
        )
        result.extend(
            (pair.pair_id, CLOSE_DONOR_LIMIT)
            for pair in ranked[max_donors:]
            if pair.status == RECOMMENDATION_PENDING
        )
    return result
