from datetime import datetime

from pydantic import BaseModel, Field, field_validator

from apps.notifications.constants import (
    HISTORY_DEFAULT_LIMIT,
    HISTORY_MAX_LIMIT,
    Severity,
)
from core.settings import get_settings


class ListNotificationsQuery(BaseModel):
    """Фильтры истории; ``ngdu_id`` — org.id из /org/v1/ngdu."""

    before_id: int | None = None
    limit: int = Field(default=HISTORY_DEFAULT_LIMIT, ge=1, le=HISTORY_MAX_LIMIT)
    created_from: datetime | None = None
    created_to: datetime | None = None
    severities: list[Severity] = Field(default_factory=list)
    event_codes: list[str] = Field(default_factory=list)
    viewed: bool | None = None
    ngdu_id: int | None = None
    well_id: int | None = None
    source_type: str | None = None
    source_id: int | None = None

    @field_validator("created_from", "created_to")
    @classmethod
    def _to_local_naive(cls, value: datetime | None) -> datetime | None:
        """Журнал хранит местное наивное время; время с поясом переводим."""
        if value is None or value.tzinfo is None:
            return value
        return value.astimezone(get_settings().ZONE_INFO).replace(tzinfo=None)


class NotificationFilterDTO(BaseModel):
    """Фильтры на уровне журнала: НГДУ уже переведено в ABAI-id."""

    before_id: int | None = None
    limit: int = HISTORY_DEFAULT_LIMIT
    created_from: datetime | None = None
    created_to: datetime | None = None
    severities: list[str] = Field(default_factory=list)
    event_codes: list[str] = Field(default_factory=list)
    viewed: bool | None = None
    abai_ngdu_id: int | None = None
    well_id: int | None = None
    source_type: str | None = None
    source_id: int | None = None
