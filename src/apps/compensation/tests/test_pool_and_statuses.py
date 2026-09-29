"""Разбор строки пула, статус донора и переходы согласования."""

import json
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from apps.compensation.constants import (
    DONOR_RESERVE,
    RECOMMENDATION_ACCEPTED,
    RECOMMENDATION_APPLIED,
    RECOMMENDATION_PENDING,
    RECOMMENDATION_REJECTED,
    RISK_LOW,
)
from apps.compensation.services.allocation import target_speed
from apps.compensation.services.approval import (
    RecommendationTransitionError,
    check_transition,
)
from apps.compensation.services.view import donor_status
from apps.compensation.tasks.import_donor_pool.import_donor_pool import (
    DEFAULT_FILE,
    donor_dto,
)
from shared.constants.ngdu import AbaiNGDUIDsEnum


def _pair(status: str, *, closed: bool = False) -> SimpleNamespace:
    closed_at = datetime(2026, 9, 29) if closed else None  # noqa: DTZ001
    return SimpleNamespace(id=1, status=status, closed_at=closed_at)


def test_pool_file_row_becomes_donor() -> None:
    data = json.loads(DEFAULT_FILE.read_text(encoding="utf-8"))
    row = data["donors"][0]  # UVK_0468: ШГН, ступень 15 %, прирост 3.44

    dto = donor_dto(row, well_id=7418, pool_date=date(2026, 9, 2))

    assert len(data["donors"]) == 66
    assert dto.abai_ngdu_id == AbaiNGDUIDsEnum.ZHMG
    assert dto.risk == RISK_LOW
    assert dto.speed_margin_checked is False
    assert (dto.speed, dto.gain, dto.lift_type) == (5.5, 3.44, "ШГН")
    assert dto.source_row == row


def test_target_speed_like_mockup() -> None:
    assert target_speed(5.5, 15) == 6.3
    assert target_speed(140, 10) == 154.0
    assert target_speed(125, 15) == 143.8


def test_donor_status() -> None:
    assert donor_status(_pair(RECOMMENDATION_PENDING), None) == RECOMMENDATION_PENDING
    assert donor_status(None, _pair(RECOMMENDATION_REJECTED, closed=True)) == (
        RECOMMENDATION_REJECTED
    )
    assert donor_status(None, _pair(RECOMMENDATION_PENDING, closed=True)) == (
        DONOR_RESERVE
    )
    assert donor_status(None, None) == DONOR_RESERVE


def test_approval_transitions() -> None:
    check_transition(_pair(RECOMMENDATION_PENDING), RECOMMENDATION_ACCEPTED)
    check_transition(_pair(RECOMMENDATION_ACCEPTED), RECOMMENDATION_REJECTED)

    with pytest.raises(RecommendationTransitionError):
        check_transition(_pair(RECOMMENDATION_APPLIED), RECOMMENDATION_REJECTED)
    with pytest.raises(RecommendationTransitionError):
        check_transition(
            _pair(RECOMMENDATION_PENDING, closed=True),
            RECOMMENDATION_ACCEPTED,
        )
