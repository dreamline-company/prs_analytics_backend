from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class UpsertDailySheetDTO(RepositoryDTO):
    """Полная строка ведомости; конфликт по (правило, НГДУ, дата) — перезапись."""

    detector_code: str
    abai_ngdu_id: int
    sheet_date: date
    oil_field_prefixes: str = ""
    status: str
    file_id: int | None = None
    rows_count: int = 0
    coverage: dict | None = None
    content: dict | None = None
    built_at: datetime | None = None
    error: str | None = None
    config_version: str


class UpdateDailySheetDTO(RepositoryDTO):
    status: str | None = None
    file_id: int | None = None
    rows_count: int | None = None
    coverage: dict | None = None
    content: dict | None = None
    built_at: datetime | None = None
    error: str | None = None
    config_version: str | None = None
