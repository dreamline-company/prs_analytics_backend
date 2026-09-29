"""Согласование пар технологом. Эндпоинта пока нет — процесс не утверждён.

Принять можно пару «на согласовании»; отклонить — ещё не применённую. Отклонённая
пара закрывается, и этот донор этой потере больше не предлагается (см.
``allocate(excluded=...)``).
"""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import (
    CLOSE_REJECTED,
    RECOMMENDATION_ACCEPTED,
    RECOMMENDATION_PENDING,
    RECOMMENDATION_REJECTED,
)
from apps.compensation.dto.internal.repositories.compensation import (
    UpdateCompensationRecommendationDTO,
)
from apps.compensation.models.compensation import CompensationRecommendation
from apps.compensation.repositories import CompensationRecommendationRepository

_ALLOWED_FROM: dict[str, frozenset[str]] = {
    RECOMMENDATION_ACCEPTED: frozenset({RECOMMENDATION_PENDING}),
    RECOMMENDATION_REJECTED: frozenset(
        {RECOMMENDATION_PENDING, RECOMMENDATION_ACCEPTED},
    ),
}


class RecommendationTransitionError(ValueError):
    """Переход статуса недопустим (пара закрыта или уже в другом статусе)."""


def check_transition(pair: CompensationRecommendation, new_status: str) -> None:
    if pair.closed_at is not None or pair.status not in _ALLOWED_FROM[new_status]:
        msg = f"{pair.status} -> {new_status} is not allowed for pair {pair.id}"
        raise RecommendationTransitionError(msg)


class CompensationApprovalService:
    def __init__(self, session: AsyncSession) -> None:
        self.repository = CompensationRecommendationRepository(session)

    async def decide(
        self,
        pair_id: int,
        *,
        status: str,
        decided_by: str,
        now: datetime,
        comment: str | None = None,
    ) -> CompensationRecommendation:
        pair = await self.repository.get_by_id(pair_id)
        if pair is None:
            msg = f"pair {pair_id} not found"
            raise RecommendationTransitionError(msg)
        check_transition(pair, status)
        rejected = status == RECOMMENDATION_REJECTED
        return await self.repository.update(
            UpdateCompensationRecommendationDTO(
                status=status,
                decided_at=now,
                decided_by=decided_by,
                comment=comment,
                closed_at=now if rejected else None,
                close_reason=CLOSE_REJECTED if rejected else None,
            ),
            filters=(CompensationRecommendation.id == pair_id,),
            exclude_none=True,
        )
