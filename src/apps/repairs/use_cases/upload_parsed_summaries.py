from sqlalchemy.ext.asyncio import AsyncSession
from starlette import status

from apps.repairs.dto.internal.repositories.reports import CreateRepairSummaryDTO
from apps.repairs.dto.requests.summaries import (
    UploadParsedSummariesListDTO,
    UploadParsedSummaryDTO,
)
from apps.repairs.repositories.reports import RepairSummaryRepository
from apps.wells.repositories import WellRepository
from shared.errors import HttpError


class WellsNotFoundError(HttpError):
    message = "Some wells referenced by summaries were not found."
    code = "wells_not_found"
    status_code = status.HTTP_400_BAD_REQUEST


class UploadParsedSummariesUseCase:
    def __init__(
        self,
        session: AsyncSession,
        well_repository: WellRepository,
        repair_summary_repository: RepairSummaryRepository,
    ) -> None:
        self.session = session
        self.well_repository = well_repository
        self.repair_summary_repository = repair_summary_repository

    async def execute(self, payload: UploadParsedSummariesListDTO) -> int:
        summaries = payload.summaries
        if not summaries:
            return 0

        wells_by_name = await self._resolve_wells(summaries)

        repo = self.repair_summary_repository
        existing_pairs = await repo.list_existing_well_date_pairs(
            (wells_by_name[s.well_name].id, s.start_date) for s in summaries
        )
        new_dtos = self._build_create_dtos(
            summaries,
            wells_by_name,
            existing_pairs=existing_pairs,
        )

        if new_dtos:
            await self.repair_summary_repository.bulk_create(new_dtos)

        await self.session.commit()
        return len(new_dtos)

    async def _resolve_wells(
        self,
        summaries: list[UploadParsedSummaryDTO],
    ) -> dict[str, object]:
        names: set[str] = set()
        for summary in summaries:
            names.add(summary.well_name)
            if summary.second_well_name:
                names.add(summary.second_well_name)

        wells = await self.well_repository.list_by_names(list(names))
        wells_by_name = {well.name: well for well in wells}

        missing = names - wells_by_name.keys()
        if missing:
            raise WellsNotFoundError(details={"well_names": sorted(missing)})

        return wells_by_name

    @staticmethod
    def _build_create_dtos(
        summaries: list[UploadParsedSummaryDTO],
        wells_by_name: dict[str, object],
        *,
        existing_pairs: set,
    ) -> list[CreateRepairSummaryDTO]:
        result: list[CreateRepairSummaryDTO] = []
        for summary in summaries:
            well_id = wells_by_name[summary.well_name].id
            if (well_id, summary.start_date) in existing_pairs:
                continue

            second_well_id = (
                wells_by_name[summary.second_well_name].id
                if summary.second_well_name
                else None
            )

            result.append(
                CreateRepairSummaryDTO(
                    well_id=well_id,
                    second_well_id=second_well_id,
                    date=summary.start_date,
                    brigade_number=summary.brigade_number,
                    pump_type=summary.pump_type,
                    shift_type_number=summary.shift_type_number,
                    car=summary.car,
                    device_number=summary.device_number,
                    shift_details=summary.shift_details,
                ),
            )

        return result
