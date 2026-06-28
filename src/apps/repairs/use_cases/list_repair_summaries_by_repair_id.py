from apps.repairs.dto.internal.summary import RepairSummaryDTO
from apps.repairs.dto.queries.summary import ListRepairSummariesByRepairIdQuery
from apps.repairs.repositories.reports import RepairSummaryRepository


class ListRepairSummariesByRepairIdUseCase:
    def __init__(
        self,
        repair_summary_repository: RepairSummaryRepository,
    ) -> None:
        self.repair_summary_repository = repair_summary_repository

    async def execute(
        self,
        query: ListRepairSummariesByRepairIdQuery,
    ) -> list[RepairSummaryDTO]:
        summaries = await self.repair_summary_repository.list_by_repair_id(
            query.repair_id,
        )
        return [RepairSummaryDTO.model_validate(summary) for summary in summaries]
