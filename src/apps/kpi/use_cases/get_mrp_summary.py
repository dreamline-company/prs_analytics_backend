"""МРП по методике КМГ и ЧРФ для НГДУ или месторождения.

Показатели считаются на том же наборе скважин, что и сводка НГДУ
(``NGDUWellsService.list_wells``), но без её правила «у Кайнармунайгаза только
VMB»: в отчётах заказчика участвуют все месторождения НГДУ.
"""

from datetime import datetime, timedelta

from apps.kpi.dto.internal.mrp import (
    CrfDTO,
    CrfWellDTO,
    MrpGroupDTO,
    MrpSummaryDTO,
)
from apps.kpi.dto.queries.mrp import GetMrpSummaryQuery
from apps.repairs.services.mrp import (
    KmgGroup,
    compute_kmg,
    month_start,
    next_month,
    window_months,
)
from apps.repairs.services.mrp_inputs import MrpInputsService
from apps.repairs.services.repair_classifier import GTM_REPAIR_TYPE_ID
from apps.wells.services import NGDUWellsService
from core.settings import get_settings

NOTE = (
    "ГТМ определяются автоматически по виду работ и тексту ремонта (оценка, "
    "около 80% совпадения с ручной разметкой заказчика; вид работ "
    f"{GTM_REPAIR_TYPE_ID} «По мероприятию» всегда ГТМ). Признак «по технической "
    "причине» в данных отсутствует, поэтому из отказов не вычитается. Фонд месяца — "
    "скважины с плановым дебитом жидкости > 0 в тех. режиме на 1-е число, время "
    "работы — их время в статусах «в работе» (WRK/PEXP) за вычетом ремонтов."
)


def _round(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


def _group_dto(group: KmgGroup) -> MrpGroupDTO:
    return MrpGroupDTO(
        key=group.key,
        wells=group.wells,
        avg_fund=round(group.avg_fund, 1),
        calendar_days=round(group.calendar_days, 1),
        work_days=round(group.work_days, 1),
        repairs=group.repairs,
        gtm=group.gtm,
        failures=group.failures,
        mrp_with_gtm=_round(group.mrp_with_gtm),
        mrp_without_gtm=_round(group.mrp_without_gtm),
    )


def default_end_month(now: datetime) -> tuple[int, int]:
    """Прошлый полный месяц: текущий ещё не закончился."""
    year, month = (now.year, now.month - 1) if now.month > 1 else (now.year - 1, 12)
    return year, month


class GetMrpSummaryUseCase:
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
        query: GetMrpSummaryQuery,
        *,
        now: datetime | None = None,
    ) -> MrpSummaryDTO:
        """``now`` — наивное местное время; параметр для воспроизводимых расчётов."""
        now = now or datetime.now(get_settings().ZONE_INFO).replace(tzinfo=None)
        if query.end_month is None:
            end_year, end_month = default_end_month(now)
        else:
            end_year, end_month = (int(part) for part in query.end_month.split("-"))

        periods = window_months(end_year, end_month, query.months)
        window_start = month_start(*periods[0])
        window_end = next_month(*periods[-1])

        wells = await self.ngdu_wells_service.list_wells(
            query.ngdu_id,
            oil_field_id=query.oil_field_id,
        )
        inputs = await self.mrp_inputs_service.load_fund_inputs(
            wells,
            window_start=window_start,
            window_end=window_end,
            now=now,
        )
        result = compute_kmg(
            inputs,
            end_year=end_year,
            end_month=end_month,
            months=query.months,
            crf_min_repairs=query.crf_min_repairs,
            now=now,
        )

        crf = result.crf
        return MrpSummaryDTO(
            window_start=result.window_start.date(),
            window_end=(result.window_end - timedelta(days=1)).date(),
            months=query.months,
            total=_group_dto(result.total),
            by_oil_field=[_group_dto(g) for g in result.by_oil_field],
            by_lift_type=[_group_dto(g) for g in result.by_lift_type],
            crf=CrfDTO(
                min_repairs=crf.min_repairs,
                fund_at_end=crf.fund_at_end,
                wells_count=len(crf.wells),
                repairs=crf.repairs,
                percent_of_fund=_round(crf.percent_of_fund, 2),
                wells=[
                    CrfWellDTO(name=w.name, oil_field=w.oil_field, repairs=w.repairs)
                    for w in crf.wells
                ],
                distribution=crf.distribution,
                wells_by_repairs=crf.wells_by_count,
            ),
            note=NOTE,
        )
