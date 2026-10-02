"""Суточная ведомость отклонений R2/R9/R10 для чтения наружу и хранения в JSONB."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DailySheetCoverageDTO(BaseModel):
    """Охват суток: без него пустая таблица неотличима от «данные не обработаны».

    Для СДМО (R2, R9) счёт — по станциям, для ЦИТС (R10) — по скважинам с
    замерами: ``stations_*`` тогда означают скважины.
    """

    # Откуда охват: sdmo — станции управления, cits — замеры дебита ЦИТС.
    source: str = "sdmo"
    # Станции НГДУ, привязанные к скважинам (cits — скважины с замерами за
    # окно правила).
    stations_total: int
    # Из них дали хотя бы один отсчёт за сутки ведомости.
    stations_reporting: int
    # По скольким правило дошло до конца суток (курсор); None — курсоров нет.
    stations_processed: int | None = None
    # Ведомость за текущие сутки — данные неполные.
    partial_day: bool = False


class DailySheetRowDTO(BaseModel):
    number: int
    well_id: int
    well_name: str
    category: str | None
    detected_at: datetime
    deviation: str
    cause: str
    probability_percent: int
    # «Дебит / Техрежим по жидкости» одной строкой, как в бланке.
    rates: str
    plan_oil: str
    # Служебное для UI: эпизоды строки, состояние главного на дату, сила.
    incident_ids: list[int]
    level: str
    status: str
    severity: str


class DailySheetTopItemDTO(BaseModel):
    rank: int
    well_name: str
    probability_percent: int
    text: str


class DailySheetOilFieldDTO(BaseModel):
    id: int
    prefix: str
    name: str


class DailySheetDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detector_code: str
    detector_name_ru: str | None
    ngdu_id: int
    ngdu_name: str
    abai_ngdu_id: int
    sheet_date: date
    # Месторождения фильтра; пустой список — ведомость по всему НГДУ.
    oil_fields: list[DailySheetOilFieldDTO] = []
    status: str
    rows_count: int
    coverage: DailySheetCoverageDTO | None
    built_at: datetime | None
    config_version: str
    file_id: int | None
    download_url: str | None = None
    expires_at: datetime | None = None
    error: str | None = None
    # Собрана этим запросом (True) или отдана из кэша.
    rebuilt: bool = False

    top: list[DailySheetTopItemDTO] = []
    attention: list[str] = []
    rows: list[DailySheetRowDTO] = []
    # R10: «замер устарел, запросить замер» — по строке на скважину.
    measure_requests: list[str] = []
    notes: list[str] = []
