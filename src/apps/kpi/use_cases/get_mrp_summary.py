"""МРП по методике КМГ и ЧРФ для НГДУ или месторождения.

Показатели считаются на том же наборе скважин, что и сводка НГДУ
(``NGDUWellsService.list_wells``), но без её правила «у Кайнармунайгаза только
VMB»: в отчётах заказчика участвуют все месторождения НГДУ.
"""

from datetime import datetime, timedelta

from apps.kpi.dto.internal.mrp import (
    CrfDTO,
    CrfLiftTypeDTO,
    CrfRepairDTO,
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
from apps.wells.services import NGDUWellsService
from core.settings import get_settings

NOTE = (
    "ГТМ — по перечню заказчика: промывка забоя, подготовка к ГИС и ГРП, ГРП, "
    "ликвидация, консервация, ввод из консервации и из бурения. «По технической "
    "причине» (замена устьевого оборудования и штока) — ремонт, но не отказ. "
    "Фонд месяца — как в ABAI: нефтяные скважины (категория OIL) в статусах "
    "«в работе», «простой» или «периодическая» на 1-е число; время работы — их "
    "время в статусах «в работе» (WRK/PEXP) за вычетом ремонтов."
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
        tech_reason=group.tech_reason,
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
        # Плановый режим ЧРФ-скважин на последний день окна — для списка ЧРФ
        crf_names = {w.name for w in crf.wells}
        abai_of = {w.name: w.abai_id for w in wells if w.name in crf_names}
        last_day = (result.window_end - timedelta(days=1)).date()
        regimes = await self.mrp_inputs_service.load_current_regimes(
            list(abai_of.values()),
            on_date=last_day,
        )
        # Горизонты на тот же день; у скважины их бывает несколько сразу
        horizons = await self.mrp_inputs_service.load_horizons(
            list(abai_of.values()),
            on_date=last_day,
        )
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
                    CrfWellDTO(
                        name=w.name,
                        oil_field=w.oil_field,
                        repairs=w.repairs,
                        lift_type=w.lift_type,
                        horizon=", ".join(horizons.get(abai_of[w.name], [])) or None,
                        regime_liquid=_round(
                            getattr(regimes.get(abai_of[w.name]), "liquid", None),
                            2,
                        ),
                        regime_oil=_round(
                            getattr(regimes.get(abai_of[w.name]), "oil", None),
                            2,
                        ),
                        regime_water_cut=_round(
                            getattr(regimes.get(abai_of[w.name]), "water_cut", None),
                            1,
                        ),
                        repairs_detail=[
                            CrfRepairDTO(
                                start=r.start,
                                end=r.end,
                                type_name=r.type_name,
                                is_gtm=r.is_gtm,
                                is_tech=r.is_tech,
                                counted=r.counted,
                            )
                            for r in w.repairs_detail
                        ],
                    )
                    for w in crf.wells
                ],
                distribution=crf.distribution,
                wells_by_repairs=crf.wells_by_count,
                shgn_fund_at_end=crf.shgn_fund_at_end,
                shgn_wells_count=crf.shgn_wells,
                shgn_repairs=crf.shgn_repairs,
                shgn_percent_of_fund=_round(crf.shgn_percent_of_fund, 2),
                by_lift_type=[
                    CrfLiftTypeDTO(
                        key=g.key,
                        fund_at_end=g.fund_at_end,
                        wells_count=g.wells,
                        repairs=g.repairs,
                        percent_of_fund=_round(g.percent_of_fund, 2),
                    )
                    for g in crf.by_lift_type
                ],
            ),
            note=NOTE,
        )
