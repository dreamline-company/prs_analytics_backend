"""Подбор доноров: месторождение, добор до покрытия, риск, расстояние, закрытие."""

from apps.compensation.constants import (
    CLOSE_DONOR_UNAVAILABLE,
    CLOSE_LOSS_RESOLVED,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from apps.compensation.services.allocation import (
    DonorInput,
    LossInput,
    NewPair,
    OpenPair,
    allocate,
    covered,
    distance_m,
    pairs_to_close,
    risk_for_step,
)

# Точки на меридиане: 0.01° широты ≈ 1112 м.
ORIGIN = (47.0, 53.0)


def _at(km: float) -> tuple[float, float]:
    return (ORIGIN[0] + km / 111.2, ORIGIN[1])


def _loss(
    well_id: int,
    value: float,
    *,
    field: str = "VMB",
    covered_value: float = 0.0,
) -> LossInput:
    return LossInput(well_id, field, value, covered_value, ORIGIN)


def _donor(
    donor_id: int,
    gain: float,
    km: float,
    *,
    risk: str = RISK_MEDIUM,
    field: str = "VMB",
) -> DonorInput:
    return DonorInput(donor_id, field, gain, risk, _at(km))


def test_risk_by_step() -> None:
    assert risk_for_step(15) == RISK_LOW
    assert risk_for_step(10) == RISK_MEDIUM
    assert risk_for_step(7) == RISK_HIGH
    assert risk_for_step(5) == RISK_HIGH


def test_distance_and_missing_coordinates() -> None:
    assert abs(distance_m(ORIGIN, _at(1)) - 1000) <= 1
    assert distance_m(ORIGIN, None) is None


def test_donors_are_added_until_loss_is_covered() -> None:
    donors = [_donor(1, 1.0, 1), _donor(2, 1.0, 2), _donor(3, 1.0, 3)]

    pairs = allocate([_loss(100, 1.5)], donors)

    assert [pair.donor_id for pair in pairs] == [1, 2]
    assert pairs[0] == NewPair(100, 1, 1000)


def test_donor_serves_one_loss_and_bigger_loss_goes_first() -> None:
    donors = [_donor(1, 1.0, 1), _donor(2, 1.0, 5)]

    pairs = allocate([_loss(100, 0.5), _loss(200, 3.0)], donors)

    assert {(pair.loss_well_id, pair.donor_id) for pair in pairs} == {
        (200, 1),
        (200, 2),
    }


def test_only_donors_of_the_same_field() -> None:
    pairs = allocate([_loss(100, 1.0)], [_donor(1, 1.0, 1, field="UAZ")])

    assert pairs == []


def test_high_risk_only_after_low_and_medium_are_exhausted() -> None:
    donors = [
        _donor(1, 0.5, 0.2, risk=RISK_HIGH),  # ближе всех, но риск высокий
        _donor(2, 0.5, 3, risk=RISK_MEDIUM),
        _donor(3, 0.5, 2, risk=RISK_LOW),
    ]

    enough = allocate([_loss(100, 1.0)], donors)
    short = allocate([_loss(100, 1.2)], donors)

    assert [pair.donor_id for pair in enough] == [3, 2]
    assert [pair.donor_id for pair in short] == [3, 2, 1]


def test_existing_coverage_and_rejected_pairs_are_respected() -> None:
    donors = [_donor(1, 1.0, 1), _donor(2, 1.0, 2)]

    covered_loss = allocate([_loss(100, 1.0, covered_value=1.0)], donors)
    partly = allocate(
        [_loss(100, 2.0, covered_value=1.2)],
        donors,
        excluded=frozenset({(100, 1)}),
    )

    assert covered_loss == []
    assert [pair.donor_id for pair in partly] == [2]


def test_donors_without_coordinates_go_last_in_their_risk_group() -> None:
    far = _donor(1, 0.5, 9)
    blind = DonorInput(2, "VMB", 0.5, RISK_MEDIUM, None)

    pairs = allocate([_loss(100, 0.8)], [blind, far])

    assert [(pair.donor_id, pair.distance_m) for pair in pairs] == [
        (1, 9000),
        (2, None),
    ]


def test_covered_is_capped_by_loss() -> None:
    assert covered(1.9, [1.13, 0.8]) == 1.9
    assert covered(3.4, [0.57, 0.42]) == 0.99


def test_pairs_to_close() -> None:
    pairs = [
        OpenPair(1, loss_well_id=100, donor_well_id=10),  # скважина запустилась
        OpenPair(2, loss_well_id=200, donor_well_id=20),  # донор встал
        OpenPair(3, loss_well_id=200, donor_well_id=30),  # всё в силе
    ]

    closing = pairs_to_close(
        pairs,
        stopped_well_ids={200},
        available_donor_well_ids={10, 30},
    )

    assert closing == [(1, CLOSE_LOSS_RESOLVED), (2, CLOSE_DONOR_UNAVAILABLE)]
