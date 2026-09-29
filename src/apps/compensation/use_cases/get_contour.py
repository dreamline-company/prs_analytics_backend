"""Контур компенсации по одной скважине: её потеря и закреплённые доноры."""

from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.compensation.constants import (
    CONTOUR_NO_PLAN,
    CONTOUR_STOPPED,
    CONTOUR_WORKING,
)
from apps.compensation.dto.internal.compensation import (
    CompensationContourCompensatedDTO,
    CompensationContourDTO,
    CompensationContourLossDTO,
)
from apps.compensation.dto.queries.compensation import GetCompensationContourQuery
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.compensation.services.allocation import covered
from apps.compensation.services.state import (
    CompensationStateService,
    to_local,
    utc_now,
)
from apps.compensation.services.view import (
    pair_donor,
    round_rate,
    stop_reason,
    well_ref,
)
from apps.wells.repositories import WellRepository
from shared.errors import HttpError


class CompensationWellNotFoundError(HttpError):
    message = "Well not found."
    code = "compensation_well_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetCompensationContourUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.well_repository = WellRepository(session)
        self.state_service = CompensationStateService(session)

    async def execute(
        self,
        query: GetCompensationContourQuery,
        *,
        now: datetime | None = None,
    ) -> CompensationContourDTO:
        now = now or utc_now()
        well = await self.well_repository.get_by_id(query.well_id)
        if well is None:
            raise CompensationWellNotFoundError(details={"well_id": query.well_id})

        contour = CompensationContourDTO(
            as_of=to_local(now),
            well=well_ref(well),
            state=CONTOUR_WORKING,
            reason=None,
            losses=None,
            compensated=CompensationContourCompensatedDTO(value=0.0, donors=0),
            efficiency_percent=None,
            potential=None,
            main_donors=[],
        )
        if await self.state_service.is_excluded_kmg_well(well):
            return contour
        state = await self.state_service.load([well], now=now)
        stop = state.stops.get(well.id)
        if stop is None:
            return contour
        contour.reason = stop_reason(stop)
        plan = state.plans.get(well.id)
        if plan is None:
            contour.state = CONTOUR_NO_PLAN
            return contour

        loss, plan_date = plan
        pairs = await CompensationRecommendationRepository(
            self.session,
        ).list_open_by_loss_well_ids([well.id])
        donors = {
            donor.id: donor
            for donor in await CompensationDonorRepository(self.session).list_by_ids(
                [pair.donor_id for pair in pairs],
            )
        }
        donor_wells = {
            item.id: item
            for item in await self.well_repository.list_by_ids(
                [donor.well_id for donor in donors.values()],
            )
        }
        value = covered(loss, [pair.gain for pair in pairs])

        contour.state = CONTOUR_STOPPED
        contour.losses = CompensationContourLossDTO(
            value=round_rate(loss),
            plan_date=plan_date,
        )
        contour.compensated = CompensationContourCompensatedDTO(
            value=round_rate(value),
            donors=len(pairs),
        )
        contour.efficiency_percent = round(value / loss * 100, 1)
        contour.potential = round_rate(loss - value)
        contour.main_donors = [
            pair_donor(
                pair,
                donors[pair.donor_id],
                donor_wells[donors[pair.donor_id].well_id],
            )
            for pair in sorted(pairs, key=lambda item: -item.gain)
        ]
        return contour
