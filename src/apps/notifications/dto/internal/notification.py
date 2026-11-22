from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator

from apps.notifications.constants import Severity
from core.settings import get_settings


class NotificationFactDTO(BaseModel):
    """Факт на момент уведомления для карточки: «Начало — 14.11 05:10»."""

    label_ru: str
    value_ru: str


class NotificationCreateDTO(BaseModel):
    """Что производитель отдаёт эмиттеру.

    ``abai_ngdu_id`` можно не задавать: у события по скважине эмиттер сам
    найдёт её текущее НГДУ. ``facts`` кладутся в ``payload["facts"]``.
    """

    event_code: str = Field(max_length=50, pattern=r"^[a-z0-9_]+(\.[a-z0-9_]+)+$")
    severity: Severity
    title_ru: str = Field(min_length=1, max_length=255)
    body_ru: str | None = None
    well_id: int | None = None
    abai_ngdu_id: int | None = None
    source_type: str | None = Field(default=None, max_length=30)
    source_id: int | None = None
    dedup_key: str = Field(min_length=1, max_length=200)
    payload: dict[str, Any] = Field(default_factory=dict)
    facts: list[NotificationFactDTO] = Field(default_factory=list)
    # Местное время события; время с поясом переводится в местное наивное.
    event_at: datetime

    @field_validator("event_at")
    @classmethod
    def _to_local_naive(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(get_settings().ZONE_INFO).replace(tzinfo=None)

    def payload_with_facts(self) -> dict[str, Any]:
        if not self.facts:
            return dict(self.payload)
        return {
            **self.payload,
            "facts": [fact.model_dump() for fact in self.facts],
        }


class NotificationRowDTO(BaseModel):
    """Строка журнала с отметкой просмотра — сырьё для элемента выдачи."""

    id: int
    event_code: str
    severity: str
    title_ru: str
    body_ru: str | None
    well_id: int | None
    abai_ngdu_id: int | None
    source_type: str | None
    source_id: int | None
    payload: dict[str, Any]
    event_at: datetime
    created_at: datetime
    viewed_at: datetime | None


class NotificationViewRowDTO(BaseModel):
    view_id: int
    notification_id: int
    viewed_at: datetime
