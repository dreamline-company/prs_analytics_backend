"""Сводка по НГДУ для шапки дашборда: фонд, добыча, план, отклонения."""

from datetime import datetime

from pydantic import BaseModel


class NgduSummaryWellsDTO(BaseModel):
    """Фонд скважин."""

    total: int
    # Станция СДМО скважины онлайн: последний регистр 1999 «Статус (VLT SALT)» = 1.
    active: int


class NgduSummaryOilDTO(BaseModel):
    """Добыча нефти, т/сут: сумма последних замеров не старше ``fresh_days``."""

    value: float
    wells_measured: int
    fresh_days: int


class NgduSummaryPlanDTO(BaseModel):
    """Выполнение плана: факт к плану техрежима по скважинам, где есть оба."""

    percent: float | None
    fact: float
    plan: float
    wells: int


class NgduSummaryDeviationsDTO(BaseModel):
    """``wells`` — скважины с уровнем alarm в матрице инцидентов (худший
    уровень активных эпизодов детекторов). ``losses`` — суммарный недобор, т/сут,
    по скважинам, где факт нефти ниже плана больше чем на ``threshold_percent``."""

    wells: int
    losses: float
    threshold_percent: float


class NgduSummaryDTO(BaseModel):
    as_of: datetime
    wells: NgduSummaryWellsDTO
    oil_production: NgduSummaryOilDTO
    plan_fulfillment: NgduSummaryPlanDTO
    deviations: NgduSummaryDeviationsDTO
