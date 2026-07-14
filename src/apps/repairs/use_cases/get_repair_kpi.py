"""Builds the KPI ПРС view for one repair from the stored ``RepairKPI`` row."""

from starlette import status

from apps.repairs.dto.internal.kpi import RepairKPIMetricsDTO, RepairKPIViewDTO
from apps.repairs.dto.queries.kpi import GetRepairKPIQuery
from apps.repairs.repositories.analytics import RepairAnalyticsRepository
from apps.repairs.repositories.kpi import RepairKPIRepository
from shared.errors import HttpError


class RepairKPINotFoundError(HttpError):
    message = "Repair KPI not found."
    code = "repair_kpi_not_found"
    status_code = status.HTTP_404_NOT_FOUND


class GetRepairKPIUseCase:
    def __init__(
        self,
        *,
        analytics_repository: RepairAnalyticsRepository,
        kpi_repository: RepairKPIRepository,
    ) -> None:
        self.analytics_repository = analytics_repository
        self.kpi_repository = kpi_repository

    async def execute(self, query: GetRepairKPIQuery) -> RepairKPIViewDTO:
        analytics = await self.analytics_repository.get_by_repair_id(query.repair_id)
        if analytics is None:
            raise RepairKPINotFoundError(details={"repair_id": query.repair_id})

        kpi = await self.kpi_repository.get_by_analytics_id(analytics.id)
        if kpi is None:
            raise RepairKPINotFoundError(
                details={
                    "repair_id": query.repair_id,
                    "analytics_id": analytics.id,
                },
            )

        metrics = RepairKPIMetricsDTO.model_validate(kpi.metrics or {})
        return RepairKPIViewDTO(
            analytics_id=analytics.id,
            repair_id=analytics.repair_id,
            status=kpi.status,
            computed_at=kpi.computed_at,
            metrics=metrics,
        )
