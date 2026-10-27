"""Динамика МРП по месяцам: показатели месяца и скользящего периода.

Набор скважин и входные данные те же, что у ``GetMrpSummaryUseCase``; окно
загрузки охватывает весь ряд вместе с первым скользящим периодом.
"""

from datetime import datetime

from apps.kpi.dto.internal.mrp import MrpDynamicsDTO, MrpDynamicsPointDTO
from apps.kpi.dto.queries.mrp import GetMrpDynamicsQuery
from apps.kpi.use_cases.get_mrp_summary import NOTE, default_end_month
from apps.repairs.services.mrp import (
    DynamicsPoint,
    compute_kmg_dynamics,
    month_start,
    next_month,
    window_months,
)
from apps.repairs.services.mrp_inputs import MrpInputsService
from apps.wells.services import NGDUWellsService
from core.settings import get_settings


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _point_dto(point: DynamicsPoint) -> MrpDynamicsPointDTO:
    return MrpDynamicsPointDTO(
        month=point.month,
        month_fund=point.month_fund,
        month_work_days=round(point.month_work_days, 1),
        month_repairs=point.month_repairs,
        month_gtm=point.month_gtm,
        month_mrp_with_gtm=_round(point.month_mrp_with_gtm),
        month_mrp_without_gtm=_round(point.month_mrp_without_gtm),
        rolling_avg_fund=round(point.rolling_avg_fund, 1),
        rolling_work_days=round(point.rolling_work_days, 1),
        rolling_repairs=point.rolling_repairs,
        rolling_gtm=point.rolling_gtm,
        rolling_mrp_with_gtm=_round(point.rolling_mrp_with_gtm),
        rolling_mrp_without_gtm=_round(point.rolling_mrp_without_gtm),
        month_tech_reason=point.month_tech,
        rolling_tech_reason=point.rolling_tech,
    )


class GetMrpDynamicsUseCase:
    def __init__(
        self,
        *,
        ngdu_wells_service: NGDUWellsService,
        mrp_inputs_service: MrpInputsService,
    ) -> None:
        self.ngdu_wells_service = ngdu_wells_service
        self.mrp_inputs_service = mrp_inputs_service

    async def execute(
        self,
        query: GetMrpDynamicsQuery,
        *,
        now: datetime | None = None,
    ) -> MrpDynamicsDTO:
        """``now`` — наивное местное время; параметр для воспроизводимых расчётов."""
        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        if query.end_month is None:
            end_year, end_month = default_end_month(now)
        else:
            end_year, end_month = (int(part) for part in query.end_month.split("-"))

        span = window_months(
            end_year,
            end_month,
            query.points + query.window_months - 1,
        )
        wells = await self.ngdu_wells_service.list_wells(
            query.ngdu_id,
            oil_field_id=query.oil_field_id,
        )
        inputs = await self.mrp_inputs_service.load_fund_inputs(
            wells,
            window_start=month_start(*span[0]),
            window_end=next_month(*span[-1]),
            now=now,
        )
        series = compute_kmg_dynamics(
            inputs,
            end_year=end_year,
            end_month=end_month,
            points=query.points,
            window_months_count=query.window_months,
            now=now,
        )
        return MrpDynamicsDTO(
            window_months=query.window_months,
            points=[_point_dto(p) for p in series],
            note=NOTE,
        )
