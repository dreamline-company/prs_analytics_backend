"""Суточная ведомость отклонений R2/R9 для чтения наружу и хранения в JSONB."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class DailySheetCoverageDTO(BaseModel):
    """Охват суток: без него пустая таблица неотличима от «данные не обработаны»."""

    # Станции НГДУ, привязанные к скважинам.
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
    recommendation: str
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


class DailySheetDTO(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    detector_code: str
    detector_name_ru: str | None
    ngdu_id: int
    ngdu_name: str
    abai_ngdu_id: int
    sheet_date: date
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
    notes: list[str] = []
