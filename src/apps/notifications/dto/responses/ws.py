"""Кадры сервера в WS уведомлений."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from apps.notifications.dto.responses.notification import (
    NotificationItemDTO,
    UnviewedCountsDTO,
)


class SnapshotFrameDTO(BaseModel):
    type: Literal["snapshot"] = "snapshot"
    # Новые сверху; клиент отбрасывает повторы по id.
    items: list[NotificationItemDTO]
    unviewed: UnviewedCountsDTO


class NotificationFrameDTO(BaseModel):
    type: Literal["notification"] = "notification"
    item: NotificationItemDTO


class ViewedFrameDTO(BaseModel):
    type: Literal["viewed"] = "viewed"
    ids: list[int]
    viewed_at: datetime
    unviewed: UnviewedCountsDTO


class PingFrameDTO(BaseModel):
    type: Literal["ping"] = "ping"
    unviewed: UnviewedCountsDTO
