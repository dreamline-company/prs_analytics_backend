"""Подбор доноров: месторождение, добор, риск, расстояние, лимит, закрытие."""

from apps.compensation.constants import (
    CLOSE_DONOR_LIMIT,
    CLOSE_DONOR_UNAVAILABLE,
    CLOSE_LOSS_RESOLVED,
    RECOMMENDATION_ACCEPTED,
    RECOMMENDATION_PENDING,
    RISK_HIGH,
    RISK_LOW,
    RISK_MEDIUM,
)
from apps.compensation.services.allocation import (
    DonorInput,
    HeldDonor,
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
    can_preempt: bool = False,
) -> LossInput:
    return LossInput(well_id, field, value, ORIGIN, can_preempt)


def _donor(
    donor_id: int,
    gain: float,
    km: float,
    *,
    risk: str = RISK_MEDIUM,
    field: str = "VMB",
) -> DonorInput:
    return DonorInput(donor_id, field, gain, risk, _at(km))


def _held(
    loss_well_id: int,
    donor: DonorInput,
    *,
    pending: bool = True,
) -> HeldDonor:
    """Донор открытой пары; id пары = id донора × 100."""
    return HeldDonor(donor.donor_id * 100, loss_well_id, donor, pending)


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

    pairs = allocate([_loss(100, 1.5)], donors).pairs

    assert [pair.donor_id for pair in pairs] == [1, 2]
    assert pairs[0] == NewPair(100, 1, 1000)


def test_donor_serves_one_loss_and_bigger_loss_goes_first() -> None:
    donors = [_donor(1, 1.0, 1), _donor(2, 1.0, 5)]

    pairs = allocate([_loss(100, 0.5), _loss(200, 3.0)], donors).pairs

    assert {(pair.loss_well_id, pair.donor_id) for pair in pairs} == {
        (200, 1),
        (200, 2),
    }


def test_only_donors_of_the_same_field() -> None:
    pairs = allocate([_loss(100, 1.0)], [_donor(1, 1.0, 1, field="UAZ")]).pairs

    assert pairs == []


def test_high_risk_only_after_low_and_medium_are_exhausted() -> None:
    donors = [
        _donor(1, 0.5, 0.2, risk=RISK_HIGH),  # ближе всех, но риск высокий
        _donor(2, 0.5, 3, risk=RISK_MEDIUM),
        _donor(3, 0.5, 2, risk=RISK_LOW),
    ]

    enough = allocate([_loss(100, 1.0)], donors).pairs
    short = allocate([_loss(100, 1.2)], donors).pairs

    assert [pair.donor_id for pair in enough] == [3, 2]
    assert [pair.donor_id for pair in short] == [3, 2, 1]


def test_existing_coverage_and_rejected_pairs_are_respected() -> None:
    donors = [_donor(1, 1.0, 1), _donor(2, 1.0, 2)]

    accepted = _donor(9, 1.0, 9)
    covered_loss = allocate(
        [_loss(100, 1.0)],
        donors,
        held=[_held(100, accepted, pending=False)],
    ).pairs
    partly = allocate(
        [_loss(100, 2.0)],
        donors,
        held=[_held(100, _donor(9, 1.2, 9), pending=False)],
        excluded=frozenset({(100, 1)}),
    ).pairs

    assert covered_loss == []
    assert [pair.donor_id for pair in partly] == [2]


def test_donors_without_coordinates_go_last_in_their_risk_group() -> None:
    far = _donor(1, 0.5, 9)
    blind = DonorInput(2, "VMB", 0.5, RISK_MEDIUM, None)

    pairs = allocate([_loss(100, 0.8)], [blind, far]).pairs

    assert [(pair.donor_id, pair.distance_m) for pair in pairs] == [
        (1, 9000),
        (2, None),
    ]


def test_covered_is_capped_by_loss() -> None:
    assert covered(1.9, [1.13, 0.8]) == 1.9
    assert covered(3.4, [0.57, 0.42]) == 0.99


def _pair(
    pair_id: int,
    loss_well_id: int,
    *,
    status: str = RECOMMENDATION_PENDING,
    risk: str = RISK_MEDIUM,
    distance: int | None = 1000,
) -> OpenPair:
    """Пара с донором на скважине ``pair_id * 10``."""
    return OpenPair(pair_id, loss_well_id, pair_id * 10, status, risk, distance)


def test_pairs_to_close() -> None:
    pairs = [
        _pair(1, loss_well_id=100),  # скважина запустилась
        _pair(2, loss_well_id=200),  # донор встал
        _pair(3, loss_well_id=200),  # всё в силе
    ]

    closing = pairs_to_close(
        pairs,
        stopped_well_ids={200},
        available_donor_well_ids={10, 30},
    )

    assert closing == [(1, CLOSE_LOSS_RESOLVED), (2, CLOSE_DONOR_UNAVAILABLE)]


def test_donors_per_loss_are_limited_and_rest_go_to_next_losses() -> None:
    donors = [_donor(i, 0.2, i) for i in range(1, 6)]

    pairs = allocate(
        [_loss(100, 5.0), _loss(200, 1.0)],
        donors,
        max_donors=2,
    ).pairs

    assert [(pair.loss_well_id, pair.donor_id) for pair in pairs] == [
        (100, 1),
        (100, 2),
        (200, 3),
        (200, 4),
    ]


def test_open_pairs_take_places_of_the_limit() -> None:
    pairs = allocate(
        [_loss(100, 5.0)],
        [_donor(1, 0.2, 1), _donor(2, 0.2, 2)],
        held=[
            _held(100, _donor(8, 0.2, 8), pending=False),
            _held(100, _donor(9, 0.2, 9), pending=False),
        ],
        max_donors=3,
    ).pairs

    assert [pair.donor_id for pair in pairs] == [1]


def test_pending_pairs_over_the_limit_are_closed_worst_first() -> None:
    pairs = [
        _pair(1, 100, risk=RISK_HIGH, distance=100),
        _pair(2, 100, distance=3000),
        _pair(3, 100, distance=None),
        _pair(4, 100, distance=500),
        _pair(5, 100, status=RECOMMENDATION_ACCEPTED, risk=RISK_HIGH),
    ]

    closing = pairs_to_close(
        pairs,
        stopped_well_ids={100},
        available_donor_well_ids={10, 20, 30, 40, 50},
        max_donors=3,
    )

    # Принятая пара остаётся вне очереди; из ожидающих — средний риск ближе.
    assert sorted(closing) == [
        (1, CLOSE_DONOR_LIMIT),
        (3, CLOSE_DONOR_LIMIT),
    ]


def test_accepted_pairs_are_never_closed_by_the_limit() -> None:
    pairs = [_pair(i, 100, status=RECOMMENDATION_ACCEPTED) for i in range(1, 5)]

    closing = pairs_to_close(
        pairs,
        stopped_well_ids={100},
        available_donor_well_ids={10, 20, 30, 40},
        max_donors=3,
    )

    assert closing == []


# --- Перехват крупной потерей (31.10.2026) ---------------------------------


def test_bigger_loss_takes_donor_of_a_smaller_one() -> None:
    donor = _donor(1, 0.5, 1)

    result = allocate(
        [_loss(100, 5.27, can_preempt=True), _loss(200, 2.06)],
        [],
        held=[_held(200, donor)],
    )

    assert result.pairs == [NewPair(100, 1, 1000, preempted_pair_id=100)]
    assert result.preempted == [100]


def test_no_preemption_between_close_losses() -> None:
    result = allocate(
        [_loss(100, 2.73, can_preempt=True), _loss(200, 2.70)],
        [],
        held=[_held(200, _donor(1, 0.5, 1))],
    )

    assert result.pairs == []


def test_accepted_pairs_are_not_taken_and_fill_the_limit() -> None:
    result = allocate(
        [_loss(100, 9.0, can_preempt=True), _loss(200, 1.0)],
        [],
        held=[
            _held(100, _donor(1, 0.2, 1), pending=False),
            _held(100, _donor(2, 0.2, 2), pending=False),
            _held(200, _donor(3, 0.2, 3), pending=False),  # принят — не трогаем
            _held(200, _donor(4, 0.2, 4)),
            _held(200, _donor(5, 0.2, 5)),
        ],
    )

    # У потери уже 2 из 3 — перехватывает одного, и не принятого.
    assert [pair.donor_id for pair in result.pairs] == [4]


def test_rejected_donor_is_not_taken() -> None:
    result = allocate(
        [_loss(100, 5.0, can_preempt=True), _loss(200, 1.0)],
        [],
        held=[_held(200, _donor(1, 0.5, 1))],
        excluded=frozenset({(100, 1)}),
    )

    assert result.pairs == []


def test_donor_of_a_loss_without_plan_is_always_taken() -> None:
    # Скважина 999 стоит, но без плана — в потерях её нет, величина 0.
    result = allocate(
        [_loss(100, 0.5, can_preempt=True)],
        [],
        held=[_held(999, _donor(1, 0.3, 1))],
    )

    assert result.preempted == [100]


def test_taken_donor_is_not_taken_again_in_the_same_run() -> None:
    result = allocate(
        [
            _loss(100, 5.0, can_preempt=True),
            _loss(200, 3.0, can_preempt=True),
            _loss(300, 1.5),
        ],
        [],
        held=[_held(300, _donor(1, 0.5, 1))],
    )

    assert [(pair.loss_well_id, pair.donor_id) for pair in result.pairs] == [(100, 1)]


def test_preemption_never_lowers_total_coverage() -> None:
    # У крупной не хватает 0,3, у мелкой донор 2,37 закрывает её целиком (1,5):
    # перехват дал бы +0,3 и −1,5.
    result = allocate(
        [_loss(100, 3.0, can_preempt=True), _loss(200, 1.5)],
        [],
        held=[
            _held(100, _donor(1, 1.35, 1), pending=False),
            _held(100, _donor(2, 1.35, 2), pending=False),
            _held(200, _donor(3, 2.37, 3)),
        ],
    )

    assert result.pairs == []


def test_short_stop_does_not_preempt_but_takes_free_donors() -> None:
    result = allocate(
        [_loss(100, 5.0, can_preempt=False), _loss(200, 1.0)],
        [_donor(1, 0.4, 5)],
        held=[_held(200, _donor(2, 0.4, 1))],
    )

    assert [(pair.donor_id, pair.preempted_pair_id) for pair in result.pairs] == [
        (1, None),
    ]


def test_smallest_loss_gives_donor_first_then_it_takes_from_smaller() -> None:
    result = allocate(
        [
            _loss(100, 6.0, can_preempt=True),
            _loss(200, 3.0, can_preempt=True),
            _loss(300, 2.0),
            _loss(400, 1.0),
        ],
        [],
        held=[
            _held(200, _donor(1, 0.5, 1)),  # ближе всех, но владелец крупнее
            _held(300, _donor(2, 0.5, 9)),
            _held(400, _donor(3, 0.5, 5)),
        ],
        max_donors=1,
    )

    # 100 забирает у самой мелкой (400); 200 лимитом занята своим донором.
    assert [(pair.loss_well_id, pair.donor_id) for pair in result.pairs] == [(100, 3)]

    cascade = allocate(
        [
            _loss(100, 6.0, can_preempt=True),
            _loss(200, 3.0, can_preempt=True),
            _loss(300, 1.0),
        ],
        [],
        held=[_held(200, _donor(1, 0.5, 1)), _held(300, _donor(2, 0.5, 2))],
        max_donors=2,
    )
    # 100 берёт обоих: сначала у 300 (мельче), затем у 200; 200 пусто — у 300
    # брать уже нечего.
    assert [(pair.loss_well_id, pair.donor_id) for pair in cascade.pairs] == [
        (100, 2),
        (100, 1),
    ]


def test_risk_order_free_then_preempt_then_high() -> None:
    donors = [
        _donor(1, 0.3, 4, risk=RISK_LOW),
        _donor(2, 0.3, 0.5, risk=RISK_HIGH),  # ближе всех, но высокий риск
    ]

    result = allocate(
        [_loss(100, 5.0, can_preempt=True), _loss(200, 1.0)],
        donors,
        held=[_held(200, _donor(3, 0.3, 2, risk=RISK_MEDIUM))],
    )

    assert [pair.donor_id for pair in result.pairs] == [1, 3, 2]


def test_second_run_on_the_result_changes_nothing() -> None:
    losses = [
        _loss(100, 5.27, can_preempt=True),
        _loss(200, 3.18, can_preempt=True),
        _loss(300, 2.06, can_preempt=True),
        _loss(400, 1.82, can_preempt=True),
    ]
    donors = {i: _donor(i, 0.3, i) for i in range(1, 8)}
    held = [
        _held(200, donors[1]),
        _held(200, donors[2]),
        _held(200, donors[3]),
        _held(300, donors[4]),
        _held(300, donors[5]),
        _held(300, donors[6]),
        _held(400, donors[7]),
    ]

    first = allocate(losses, [], held=held)
    moved = {pair.donor_id: pair.loss_well_id for pair in first.pairs}
    after = [
        _held(moved.get(item.donor.donor_id, item.loss_well_id), item.donor)
        for item in held
    ]
    second = allocate(losses, [], held=after)

    assert first.pairs  # 100 перехватила
    assert second.pairs == []


def test_victim_rejected_by_coverage_then_next_one_is_taken() -> None:
    # У крупной не хватает 0,3. Самая мелкая (1,0) закрыта донором 2,37 целиком —
    # перехват уронил бы покрытие на 0,7; следующая (1,5) отдаёт 0,3 без потерь.
    result = allocate(
        [_loss(100, 3.0, can_preempt=True), _loss(200, 1.5), _loss(300, 1.0)],
        [],
        held=[
            _held(100, _donor(1, 2.7, 1), pending=False),
            _held(300, _donor(2, 2.37, 2)),
            _held(200, _donor(3, 0.3, 3)),
        ],
    )

    assert [(pair.donor_id, pair.preempted_pair_id) for pair in result.pairs] == [
        (3, 300),
    ]


def test_high_risk_donor_is_preempted_last() -> None:
    result = allocate(
        [_loss(100, 5.0, can_preempt=True), _loss(200, 1.0)],
        [],
        held=[
            _held(200, _donor(1, 0.3, 1, risk=RISK_HIGH)),
            _held(200, _donor(2, 0.3, 9, risk=RISK_LOW)),
        ],
    )

    assert [pair.donor_id for pair in result.pairs] == [2, 1]


def test_ratio_boundary_survives_float_rounding() -> None:
    # 2,5 × 1,2 = 3,0000000000000004 — ровно 1,2 раза меньше, перехват есть.
    result = allocate(
        [_loss(100, 3.0, can_preempt=True), _loss(200, 2.5)],
        [],
        held=[_held(200, _donor(1, 0.3, 1))],
    )

    assert result.preempted == [100]
