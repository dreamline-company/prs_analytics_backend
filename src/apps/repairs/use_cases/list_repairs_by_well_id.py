from apps.repairs.dto.internal.repair import RepairDTO
from apps.repairs.dto.queries.repair import ListRepairsByWellIdQuery
from apps.repairs.repositories.repair import RepairRepository
from apps.wells.repositories import WellRepository


class ListRepairsByWellIdUseCase:
    def __init__(
        self,
        repair_repository: RepairRepository,
        well_repository: WellRepository,
    ) -> None:
        self.repair_repository = repair_repository
        self.well_repository = well_repository

    async def execute(
        self,
        query: ListRepairsByWellIdQuery,
    ) -> list[RepairDTO]:
        well = await self.well_repository.get_by_id(id_=query.well_id)
        print("Well: ", well)
        if not well:
            return []
        repairs = await self.repair_repository.list_by_well_abai_id(
            well_id=well.abai_id,
        )
        return [RepairDTO.model_validate(repair) for repair in repairs]
