"""МРП по методике КМГ (форма ИСУ KMG-F-2429.1-10) и ЧРФ."""

from datetime import date

from pydantic import BaseModel


class MrpGroupDTO(BaseModel):
    """Показатели группы скважин (НГДУ в целом, месторождение, способ добычи)."""

    key: str
    wells: int
    # Среднее по месяцам окна число скважин, проработавших в месяце
    avg_fund: float
    # Σ по месяцам (фонд месяца × дней в месяце), скв-сут
    calendar_days: float
    # Время работы: интервалы статуса «в работе» за вычетом ремонтов, скв-сут
    work_days: float
    repairs: int
    gtm: int
    # Отказы = ремонты − ГТМ (без признака «по технической причине»)
    failures: int
    # Время работы ÷ число ремонтов, сут
    mrp_with_gtm: float | None
    # Время работы ÷ число отказов, сут
    mrp_without_gtm: float | None


class CrfWellDTO(BaseModel):
    name: str
    oil_field: str
    repairs: int


class CrfDTO(BaseModel):
    """Часто ремонтируемый фонд: N и более ремонтов без ГТМ за период."""

    min_repairs: int
    # Действующий фонд на конец окна: скважины с работой в последнем месяце
    fund_at_end: int
    wells_count: int
    repairs: int
    percent_of_fund: float | None
    wells: list[CrfWellDTO]
    # Градация: число ремонтов без ГТМ → число скважин
    distribution: dict[int, int]
    # Скважины ЧРФ по числу ремонтов
    wells_by_repairs: dict[int, list[str]]


class MrpSummaryDTO(BaseModel):
    window_start: date
    window_end: date  # последний день окна
    months: int
    total: MrpGroupDTO
    by_oil_field: list[MrpGroupDTO]
    by_lift_type: list[MrpGroupDTO]
    crf: CrfDTO
    # Как определены ГТМ и чего в расчёте нет
    note: str


class MrpDynamicsPointDTO(BaseModel):
    """Один месяц ряда: показатели месяца и скользящего периода по этот месяц."""

    month: str  # YYYY-MM
    # Текущий месяц
    month_fund: int
    month_work_days: float
    month_repairs: int
    month_gtm: int
    month_mrp_with_gtm: float | None
    month_mrp_without_gtm: float | None
    # Скользящий период (``window_months`` месяцев), заканчивающийся этим месяцем
    rolling_avg_fund: float
    rolling_work_days: float
    rolling_repairs: int
    rolling_gtm: int
    rolling_mrp_with_gtm: float | None
    rolling_mrp_without_gtm: float | None


class MrpDynamicsDTO(BaseModel):
    """Динамика МРП по методике КМГ помесячно."""

    window_months: int
    points: list[MrpDynamicsPointDTO]
    note: str
