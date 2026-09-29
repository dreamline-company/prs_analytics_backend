"""Скважины-доноры НГДУ: весь пул области со статусами и шапкой для вкладок."""

from collections import Counter
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import (
    RECOMMENDATION_APPLIED,
    RECOMMENDATION_PENDING,
)
from apps.compensation.dto.internal.compensation import (
    CompensationDonorConstraintsDTO,
    CompensationDonorDTO,
    CompensationDonorsCountsDTO,
    CompensationDonorsDTO,
    CompensationDonorsSummaryDTO,
    CompensationNgduDTO,
)
from apps.compensation.dto.queries.compensation import ListCompensationDonorsQuery
from apps.compensation.repositories import CompensationRecommendationRepository
from apps.compensation.services.allocation import target_speed
from apps.compensation.services.state import utc_now
from apps.compensation.services.view import (
    donor_status,
    load_scope,
    oil_field_ref,
    round_rate,
    speed_change,
    well_ref,
)
from apps.detectors.use_cases.get_daily_sheet import display_ngdu_name
from apps.org.repositories.org import OrgRepository
from apps.wells.repositories import WellRepository


class ListCompensationDonorsUseCase:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def execute(
        self,
        query: ListCompensationDonorsQuery,
        *,
        now: datetime | None = None,
    ) -> CompensationDonorsDTO:
        scope = await load_scope(self.session, query, now=now or utc_now())
        latest = await CompensationRecommendationRepository(
            self.session,
        ).list_latest_by_donor_ids([donor.id for donor in scope.donors])
        open_by_donor = {pair.donor_id: pair for pair in scope.open_pairs}
        loss_wells = {
            well.id: well
            for well in await WellRepository(self.session).list_by_ids(
                [pair.loss_well_id for pair in scope.open_pairs],
            )
        }
        org = await OrgRepository(self.session).get_by_id(query.ngdu_id)
        ngdu = CompensationNgduDTO(
            id=query.ngdu_id,
            name=display_ngdu_name(org.name_ru) if org is not None else "",
        )
        names = scope.field_names

        items = []
        for donor in scope.donors:
            pair = open_by_donor.get(donor.id)
            well = scope.wells[donor.well_id]
            items.append(
                CompensationDonorDTO(
                    donor_id=donor.id,
                    well=well_ref(well),
                    oil_field=oil_field_ref(well, names),
                    ngdu=ngdu,
                    gzu=donor.gzu,
                    lift_type=donor.lift_type,
                    status=donor_status(pair, latest.get(donor.id)),
                    risk=donor.risk,
                    speed=speed_change(
                        donor,
                        speed_to=(
                            pair.speed_to
                            if pair is not None
                            else target_speed(donor.speed, donor.step_percent)
                        ),
                    ),
                    qn=donor.qn,
                    gain=round_rate(donor.gain),
                    constraints=CompensationDonorConstraintsDTO(
                        speed_margin_checked=donor.speed_margin_checked,
                        submergence_m=donor.submergence_m,
                        water_cut_percent=donor.water_cut,
                    ),
                    loss_well=(
                        well_ref(loss_wells[pair.loss_well_id])
                        if pair is not None
                        else None
                    ),
                ),
            )

        counts = Counter(item.status for item in items)
        available = scope.available_potential()
        used = sum(
            p.gain for p in scope.open_pairs if p.status == RECOMMENDATION_APPLIED
        )
        pool_dates = [donor.pool_date for donor in scope.donors]
        return CompensationDonorsDTO(
            summary=CompensationDonorsSummaryDTO(
                candidates=len(items),
                available_potential=round_rate(available),
                used=round_rate(used),
                awaiting_approval=round_rate(
                    sum(
                        p.gain
                        for p in scope.open_pairs
                        if p.status == RECOMMENDATION_PENDING
                    ),
                ),
                realized_percent=round(used / available * 100, 1) if available else 0.0,
                pool_calculated_at=max(pool_dates) if pool_dates else None,
            ),
            counts=CompensationDonorsCountsDTO(all=len(items), **counts),
            items=sorted(
                (item for item in items if query.status in (None, item.status)),
                key=lambda item: (-item.gain, item.well.well_name),
            ),
        )
