from datetime import datetime

from pydantic import BaseModel, ConfigDict, computed_field

from apps.detectors import labels


class IncidentVerificationDTO(BaseModel):
    """Отметка проверки эпизода независимыми данными (``detectors_verification``).

    verdict: pending | confirmed | not_confirmed (правило r2-verify-v3; у
    старых строк — false_alarm / failure_likely / failure_confirmed /
    undetermined); reason — почему (repair_rod_break, moment_recovered,
    repair_other, …); evidence — найденные доказательства (moment / oil /
    drive / abai / repair). ``verdict_ru`` / ``reason_ru`` — подписи.
    """

    model_config = ConfigDict(from_attributes=True)

    verdict: str
    reason: str | None
    is_final: bool
    evidence_at: datetime | None
    decided_at: datetime
    final_at: datetime | None
    evidence: dict | None
    rule_version: str

    @computed_field
    @property
    def verdict_ru(self) -> str | None:
        return labels.verdict_label_ru(self.verdict)

    @computed_field
    @property
    def reason_ru(self) -> str | None:
        return labels.reason_label_ru(self.reason)


class DetectorIncidentDTO(BaseModel):
    """Эпизод детекции для чтения наружу — строка ``detectors_incident``.

    ``detector_name_ru`` подставляется на чтении из реестра правил: в таблице
    инцидентов лежит только код. ``verification`` — отметка проверки, если
    эпизод уже проверялся (пока только R2).
    """

    model_config = ConfigDict(from_attributes=True)

    id: int
    detector_code: str
    detector_name_ru: str | None = None
    well_id: int
    entity_id: int | None
    reason_code: str

    level: str
    status: str

    opened_at: datetime
    detected_at: datetime
    last_seen_at: datetime
    escalated_at: datetime | None
    normalized_at: datetime | None
    close_reason: str | None

    config_version: str
    payload: dict | None

    verification: IncidentVerificationDTO | None = None

    # --- Подписи для фронта: метки графика и стадия, без сборки из кодов ---

    @computed_field
    @property
    def level_ru(self) -> str:
        """У R2 — стадия («Падение нагрузки» / «Возможный обрыв штанг»)."""
        return labels.level_label_ru(self.detector_code, self.level)

    @computed_field
    @property
    def opened_label_ru(self) -> str:
        return labels.opened_label_ru(self.detector_code, self.detector_name_ru)

    @computed_field
    @property
    def escalated_label_ru(self) -> str | None:
        if self.escalated_at is None:
            return None
        return labels.escalated_label_ru(self.detector_code, self.detector_name_ru)

    @computed_field
    @property
    def closed_label_ru(self) -> str | None:
        if self.normalized_at is None:
            return None
        return labels.closed_label_ru(self.detector_code, self.close_reason)
