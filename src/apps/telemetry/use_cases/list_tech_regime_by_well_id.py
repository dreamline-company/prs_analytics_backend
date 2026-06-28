from apps.telemetry.dto.internal.tech_regime import TechRegimeDTO
from apps.telemetry.dto.queries.tech_regime import ListTechRegimeByWellIdQuery
from apps.telemetry.repositories.tech_regime import TechRegimeRepository
from apps.wells.repositories import WellRepository


class ListTechRegimeByWellIdUseCase:
    def __init__(
        self,
        tech_regime_repository: TechRegimeRepository,
        well_repository: WellRepository,
    ) -> None:
        self.tech_regime_repository = tech_regime_repository
        self.well_repository = well_repository

    async def execute(
        self,
        query: ListTechRegimeByWellIdQuery,
    ) -> list[TechRegimeDTO]:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        if not well:
            return []

        regimes = await self.tech_regime_repository.list_by_abai_well_id_in_period(
            abai_well_id=well.abai_id,
            start_date_from=query.start_date_from,
            start_date_to=query.start_date_to,
        )
        return [TechRegimeDTO.model_validate(regime) for regime in regimes]
