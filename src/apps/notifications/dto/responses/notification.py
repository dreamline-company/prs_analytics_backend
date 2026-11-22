from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from apps.notifications.dto.internal.notification import NotificationFactDTO
from shared.dto.api import AppResponse


class UnviewedCountsDTO(BaseModel):
    """Непросмотренные по уровням — счётчик колокольчика."""

    alarm: int = 0
    warning: int = 0
    info: int = 0


class NotificationWellDTO(BaseModel):
    id: int
    name: str
    # org.id из /org/v1/ngdu, не ABAI-id; НГДУ скважины на момент записи.
    ngdu_id: int | None
    ngdu_name: str | None
    exploitation: Literal["evn", "shgn"] | None


class NotificationItemDTO(BaseModel):
    """Элемент истории, карточки и кадров WS — всё для строки и перехода."""

    id: int
    event_code: str
    severity: str
    title_ru: str
    body_ru: str | None
    event_at: datetime
    created_at: datetime
    viewed_at: datetime | None
    source_type: str | None
    source_id: int | None
    well: NotificationWellDTO | None


class NotificationChainItemDTO(BaseModel):
    id: int
    event_code: str
    severity: str
    title_ru: str
    event_at: datetime
    created_at: datetime
    viewed_at: datetime | None


class NotificationCardDTO(NotificationItemDTO):
    payload: dict[str, Any]
    facts: list[NotificationFactDTO]
    # Все уведомления того же источника по возрастанию id, включая это.
    chain: list[NotificationChainItemDTO]


class NotificationHistoryDTO(BaseModel):
    items: list[NotificationItemDTO]
    # id последнего элемента страницы для before_id; None — дальше пусто.
    next_before_id: int | None


class NotificationViewResultDTO(BaseModel):
    # Что отметил этот вызов; пусто — уже было просмотрено.
    ids: list[int]
    viewed_at: datetime | None
    unviewed: UnviewedCountsDTO


class NotificationHistoryResponseDTO(AppResponse[NotificationHistoryDTO]):
    pass


class NotificationCardResponseDTO(AppResponse[NotificationCardDTO]):
    pass


class NotificationViewResponseDTO(AppResponse[NotificationViewResultDTO]):
    pass
