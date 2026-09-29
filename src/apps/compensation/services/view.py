"""Общая выборка контура по НГДУ и сборка частей ответа."""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from apps.compensation.constants import (
    DONOR_RESERVE,
    RECOMMENDATION_REJECTED,
    SPEED_UNITS,
)
from apps.compensation.dto.internal.compensation import (
    CompensationOilFieldDTO,
    CompensationPairDonorDTO,
    CompensationSpeedDTO,
    CompensationStopReasonDTO,
    CompensationWellDTO,
)
from apps.compensation.dto.queries.compensation import ListCompensationQuery
from apps.compensation.models.compensation import (
    CompensationDonor,
    CompensationRecommendation,
)
from apps.compensation.repositories import (
    CompensationDonorRepository,
    CompensationRecommendationRepository,
)
from apps.compensation.services.state import (
    CompensationStateService,
    Stop,
    WellsState,
)
from apps.org.services import well_name_prefix
from apps.wells.models.well import Well


@dataclass(slots=True)
class ScopeView:
    wells: dict[int, Well]
    state: WellsState
    donors: list[CompensationDonor]
    open_pairs: list[CompensationRecommendation]  # открытые пары доноров области
    available: set[int]  # well_id работающих доноров без аварии

    @property
    def donor_by_id(self) -> dict[int, CompensationDonor]:
        return {donor.id: donor for donor in self.donors}

    @property
    def field_names(self) -> dict[str, str]:
        """Префикс -> название месторождения из пула («Вос. Молдабек»)."""
        return {
            well_name_prefix(self.wells[donor.well_id].name) or "": donor.oil_field_name
            for donor in self.donors
        }

    def available_potential(self) -> float:
        return sum(
            donor.gain for donor in self.donors if donor.well_id in self.available
        )


async def load_scope(
    session: AsyncSession,
    query: ListCompensationQuery,
    *,
    now: datetime,
) -> ScopeView:
    state_service = CompensationStateService(session)
    wells = {
        well.id: well
        for well in await state_service.scope_wells(query.ngdu_id, query.oil_field_id)
    }
    state = await state_service.load(list(wells.values()), now=now)
    donors = list(
        await CompensationDonorRepository(session).list_by_well_ids(list(wells)),
    )
    open_pairs = await CompensationRecommendationRepository(
        session,
    ).list_open_by_donor_ids([donor.id for donor in donors])
    return ScopeView(
        wells=wells,
        state=state,
        donors=donors,
        open_pairs=list(open_pairs),
        available={
            donor.well_id
            for donor in donors
            if donor.well_id not in state.stops and donor.well_id not in state.alarms
        },
    )


def round_rate(value: float) -> float:
    """Округление т/сут для ответа."""
    return round(value, 2)


def well_ref(well: Well) -> CompensationWellDTO:
    return CompensationWellDTO(id=well.id, well_name=well.name)


def oil_field_ref(well: Well, names: dict[str, str]) -> CompensationOilFieldDTO:
    prefix = well_name_prefix(well.name) or ""
    return CompensationOilFieldDTO(prefix=prefix, name=names.get(prefix, prefix))


def speed_change(
    donor: CompensationDonor,
    *,
    speed_to: float,
) -> CompensationSpeedDTO:
    return CompensationSpeedDTO(
        from_=donor.speed,
        to=speed_to,
        delta=round(speed_to - donor.speed, 1),
        step_percent=donor.step_percent,
        units=SPEED_UNITS.get(donor.lift_type),
    )


def stop_reason(stop: Stop) -> CompensationStopReasonDTO:
    return CompensationStopReasonDTO(kind=stop.kind, text=stop.text, since=stop.since)


def pair_donor(
    pair: CompensationRecommendation,
    donor: CompensationDonor,
    donor_well: Well,
) -> CompensationPairDonorDTO:
    return CompensationPairDonorDTO(
        recommendation_id=pair.id,
        well=well_ref(donor_well),
        gain=round_rate(pair.gain),
        risk=donor.risk,
        distance_m=pair.distance_m,
        status=pair.status,
    )


def donor_status(
    open_pair: CompensationRecommendation | None,
    latest_pair: CompensationRecommendation | None,
) -> str:
    """Статус донора: его открытой пары; без пары — «отклонён», если последнюю
    отклонили, иначе «в резерве»."""
    if open_pair is not None:
        return open_pair.status
    if latest_pair is not None and latest_pair.status == RECOMMENDATION_REJECTED:
        return RECOMMENDATION_REJECTED
    return DONOR_RESERVE
