from dataclasses import asdict
from datetime import datetime

from apps.wells.dto.internal.well_production_profile import (
    ProfilePeriod,
    WellProductionProfileDTO,
)
from apps.wells.repositories import WellRepository
from apps.wells.services.production_profile import WellProductionProfileService
from apps.wells.use_cases.get_well_card import WellNotFoundError
from core.settings import get_settings


class GetWellProductionProfileUseCase:
    def __init__(
        self,
        *,
        well_repository: WellRepository,
        production_profile_service: WellProductionProfileService,
    ) -> None:
        self.well_repository = well_repository
        self.production_profile_service = production_profile_service

    async def execute(
        self,
        well_id: int,
        period: ProfilePeriod,
    ) -> WellProductionProfileDTO:
        well = await self.well_repository.get_by_id(id_=well_id)
        if well is None:
            raise WellNotFoundError(details={"well_id": well_id})
        profile = await self.production_profile_service.get(
            well_id=well.id,
            abai_well_id=well.abai_id,
            period=period,
            now=datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None),
        )
        return WellProductionProfileDTO(
            well_id=well.id,
            well_name=well.name,
            **asdict(profile),
        )
