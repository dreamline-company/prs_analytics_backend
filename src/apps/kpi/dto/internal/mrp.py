"""МРП по методике КМГ (форма ИСУ KMG-F-2429.1-10) и ЧРФ."""

from datetime import date, datetime

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
    # Отказы = ремонты − ГТМ − «по технической причине»
    failures: int
    # Время работы ÷ число ремонтов, сут
    mrp_with_gtm: float | None
    # Время работы ÷ число отказов, сут
    mrp_without_gtm: float | None
    # Ремонты «по технической причине» (замена устья, штока): не отказы
    tech_reason: int = 0


class CrfRepairDTO(BaseModel):
    """Ремонт скважины ЧРФ за период: заказчик проверяет, какие ремонты учтены."""

    start: datetime
    end: datetime | None
    type_name: str | None
    is_gtm: bool
    is_tech: bool
    # Учтён в числе ремонтов ЧРФ (не ГТМ и скважина в фонде в месяц начала)
    counted: bool


class CrfWellDTO(BaseModel):
    name: str
    oil_field: str
    repairs: int
    lift_type: str | None = None
    # Плановый тех. режим на конец периода: жидкость, нефть, обводнённость
    regime_liquid: float | None = None
    regime_oil: float | None = None
    regime_water_cut: float | None = None
    # Горизонт в нашей БД пока не хранится
    horizon: str | None = None
    repairs_detail: list[CrfRepairDTO] = []


class CrfLiftTypeDTO(BaseModel):
    """ЧРФ одного способа добычи: фонд способа, скважины ЧРФ, ремонты, доля."""

    key: str
    fund_at_end: int
    wells_count: int
    repairs: int
    percent_of_fund: float | None


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
    # ЧРФ по фонду ШГН: фонд ШГН на конец окна, скважины, ремонты, доля от фонда ШГН
    shgn_fund_at_end: int = 0
    shgn_wells_count: int = 0
    shgn_repairs: int = 0
    shgn_percent_of_fund: float | None = None
    # ЧРФ по способам добычи (ШГН, ЭВН, УЭЦН, фонтан…), по убыванию фонда способа
    by_lift_type: list[CrfLiftTypeDTO] = []


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
    month_tech_reason: int = 0
    rolling_tech_reason: int = 0


class MrpDynamicsDTO(BaseModel):
    """Динамика МРП по методике КМГ помесячно."""

    window_months: int
    points: list[MrpDynamicsPointDTO]
    note: str
