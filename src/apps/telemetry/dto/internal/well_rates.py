from datetime import date, datetime

from pydantic import BaseModel


class WellRatesDTO(BaseModel):
    """Дебиты скважины: факт по последнему отсчёту и план по последнему режиму.

    Общая часть паспорта — одна и та же в карточке скважины и в строке матрицы,
    чтобы обводнённость считалась одним кодом и цифры не расходились.
    """

    # Telemetry (последняя запись по скважине).
    oil_rate: float | None  # Дебит нефти — Telemetry.qm_oil
    liquid_rate: float | None  # Дебит жидкости — Telemetry.qv_liquid
    water_cut: float | None  # Обводнённость, % — считается из дебитов
    telemetry_time: datetime | None  # Время замера — Telemetry.date_time
    # TechRegime (последний режим по скважине).
    plan_oil_rate: float | None  # План Qн — TechRegime.oil
    plan_liquid_rate: float | None  # План Qж — TechRegime.liquid
    tech_regime_date: date | None  # Начало режима — TechRegime.start_date
