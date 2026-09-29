"""Рекомендации по донорам НГДУ: открытые пары, крупные сначала."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import RECOMMENDATION_APPLIED
from apps.compensation.dto.internal.compensation import (
    CompensationRecommendationDTO,
    CompensationRecommendationsDTO,
)
from apps.compensation.dto.queries.compensation import (
    ListCompensationRecommendationsQuery,
)
from apps.compensation.services.state import utc_now
from apps.compensation.services.view import (
    load_scope,
    round_rate,
    speed_change,
    well_ref,
)
from apps.wells.repositories import WellRepository


class ListCompensationRecommendationsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        query: ListCompensationRecommendationsQuery,
        *,
        now: datetime | None = None,
    ) -> CompensationRecommendationsDTO:
        scope = await load_scope(self.session, query, now=now or utc_now())
        donors = scope.donor_by_id
        pairs = sorted(scope.open_pairs, key=lambda pair: (-pair.gain, pair.id))
        loss_wells = {
            well.id: well
            for well in await WellRepository(self.session).list_by_ids(
                [pair.loss_well_id for pair in pairs],
            )
        }
        return CompensationRecommendationsDTO(
            available_potential=round_rate(scope.available_potential()),
            used=round_rate(
                sum(p.gain for p in pairs if p.status == RECOMMENDATION_APPLIED),
            ),
            total=len(pairs),
            items=[
                CompensationRecommendationDTO(
                    recommendation_id=pair.id,
                    status=pair.status,
                    donor=well_ref(scope.wells[donors[pair.donor_id].well_id]),
                    speed=speed_change(donors[pair.donor_id], speed_to=pair.speed_to),
                    gain=round_rate(pair.gain),
                    risk=donors[pair.donor_id].risk,
                    loss_well=well_ref(loss_wells[pair.loss_well_id]),
                    distance_m=pair.distance_m,
                )
                for pair in pairs[: query.limit]
            ],
        )
