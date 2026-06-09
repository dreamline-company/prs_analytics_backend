from apps.kpi.dto.internal.main_kpi import MainKPIIsNewDTO, MainKPIStatusDTO
from apps.kpi.dto.queries.main_kpi import GetMainKpiStatusUpdatesCommand


class GetMainKpiNewStatusUseCase:
    async def execute(
        self,
        query: GetMainKpiStatusUpdatesCommand,
    ) -> MainKPIIsNewDTO:
        is_new = True
        return MainKPIIsNewDTO(
            data=MainKPIStatusDTO(
                stops=3,
                disconnecting=5,
                accident_risk=3,
                oil_production_loss=5,
                losses_compensation=6,
            ),
            is_new=is_new,
        )
