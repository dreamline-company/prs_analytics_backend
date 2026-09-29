"""Стоящие скважины НГДУ: потеря, закреплённые доноры, непокрытый остаток."""

from collections import defaultdict
from datetime import datetime
from typing import TYPE_CHECKING

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.dto.internal.compensation import (
    CompensationLossDTO,
    CompensationLossesDTO,
    CompensationLossesSummaryDTO,
)
from apps.compensation.dto.queries.compensation import ListCompensationQuery
from apps.compensation.services.allocation import covered
from apps.compensation.services.state import utc_now
from apps.compensation.services.view import (
    load_scope,
    oil_field_ref,
    pair_donor,
    round_rate,
    stop_reason,
    well_ref,
)

if TYPE_CHECKING:
    from apps.compensation.models.compensation import CompensationRecommendation


class ListCompensationLossesUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        query: ListCompensationQuery,
        *,
        now: datetime | None = None,
    ) -> CompensationLossesDTO:
        scope = await load_scope(self.session, query, now=now or utc_now())
        donors = scope.donor_by_id
        names = scope.field_names
        pairs_by_loss: dict[int, list[CompensationRecommendation]] = defaultdict(list)
        for pair in scope.open_pairs:
            pairs_by_loss[pair.loss_well_id].append(pair)

        items = []
        for well_id, stop in scope.state.stops.items():
            well = scope.wells[well_id]
            pairs = sorted(pairs_by_loss.get(well_id, []), key=lambda p: -p.gain)
            plan = scope.state.plans.get(well_id)
            loss, plan_date = plan if plan is not None else (None, None)
            value = covered(loss, [p.gain for p in pairs]) if loss is not None else 0.0
            items.append(
                CompensationLossDTO(
                    well=well_ref(well),
                    oil_field=oil_field_ref(well, names),
                    reason=stop_reason(stop),
                    loss=round_rate(loss) if loss is not None else None,
                    plan_date=plan_date,
                    covered=round_rate(value),
                    uncovered=round_rate(loss - value) if loss is not None else None,
                    donors=[
                        pair_donor(
                            pair,
                            donors[pair.donor_id],
                            scope.wells[donors[pair.donor_id].well_id],
                        )
                        for pair in pairs
                    ],
                ),
            )
        items.sort(
            key=lambda item: (
                item.loss is None,
                -(item.loss or 0),
                item.well.well_name,
            ),
        )

        planned = [item for item in items if item.loss is not None]
        loss_sum = sum(item.loss for item in planned)
        covered_sum = sum(item.covered for item in planned)
        return CompensationLossesDTO(
            summary=CompensationLossesSummaryDTO(
                wells=len(planned),
                wells_without_plan=len(items) - len(planned),
                loss=round_rate(loss_sum),
                covered=round_rate(covered_sum),
                uncovered=round_rate(loss_sum - covered_sum),
            ),
            items=items,
        )
