from datetime import date, datetime

from shared.dto.repositories import RepositoryDTO


class UpsertR2LossReportDTO(RepositoryDTO):
    report_date: date
    status: str
    file_id: int | None = None
    wells_count: int = 0
    total_oil_loss: float = 0
    content: list | None = None
    built_at: datetime | None = None
    error: str | None = None
    config_version: str


class UpdateR2LossReportMailDTO(RepositoryDTO):
    mail_status: str
    mail_recipients: list[str] | None = None
    mail_sent_at: datetime | None = None
    mail_error: str | None = None
