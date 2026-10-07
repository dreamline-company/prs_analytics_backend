from datetime import datetime

from shared.dto.repositories import RepositoryDTO


class CreateVerificationDTO(RepositoryDTO):
    incident_id: int
    well_id: int
    detector_code: str
    verdict: str
    reason: str | None = None
    is_final: bool = False
    evidence_at: datetime | None = None
    decided_at: datetime
    final_at: datetime | None = None
    evidence: dict | None = None
    rule_version: str


class UpdateVerificationDTO(RepositoryDTO):
    verdict: str | None = None
    reason: str | None = None
    is_final: bool | None = None
    evidence_at: datetime | None = None
    decided_at: datetime | None = None
    final_at: datetime | None = None
    evidence: dict | None = None
    rule_version: str | None = None


class CreateVerificationHistoryDTO(RepositoryDTO):
    verification_id: int
    verdict_from: str | None = None
    verdict_to: str
    reason_from: str | None = None
    reason_to: str | None = None
    is_final: bool
    evidence: dict | None = None
    changed_at: datetime
    rule_version: str


class UpdateVerificationHistoryDTO(RepositoryDTO):
    """Журнал только дописывается — обновлять нечего."""
